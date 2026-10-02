"""Local TLS regressions; only s_client is stubbed in parser tests."""
from __future__ import annotations

import os
from pathlib import Path
import pty
import shutil
import socket
import ssl
import threading
import time
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]


class TLS(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(dir=os.environ.get('TMPDIR'))
        self.addCleanup(self.tmp.cleanup)
        self.path = Path(self.tmp.name)
        openssl = shutil.which('openssl')
        assert openssl is not None
        self.openssl: str = openssl
        stub = self.path / 'openssl'
        stub.write_text('''#!/bin/bash
if [[ $1 == s_client ]]; then
  printf '%s\\n' "$@" > "$TLS_ARGS"
  if [[ -n ${TLS_CERT:-} ]]; then
    echo 'verification diagnostic, not certificate data' >&2
    cat "$TLS_CERT"
    exit 0
  fi
  echo 'fixture connection refused' >&2
  exit 1
fi
exec "$REAL_OPENSSL" "$@"
''')
        stub.chmod(0o755)
        self.env = dict(os.environ, PATH=f'{self.path}:{os.environ["PATH"]}',
                        REAL_OPENSSL=self.openssl, TLS_ARGS=str(self.path / 'args'),
                        TERM='', NO_COLOR='1', TMPDIR=str(self.path))

    def run_tool(self, tool, *args, env=None):
        result = subprocess.run([str(ROOT / 'linux' / tool), *args],
                                env=env or self.env, text=True, capture_output=True, timeout=8)
        self.assertEqual(list(self.path.glob('tls.*')), [], 'temporary files leaked')
        return result

    def certificate(self, subject='/O=Fixture, Inc./OU=Testing/CN=example.test',
                    san: str | None = 'DNS:example.test,DNS:alt.test,IP:192.0.2.7,IP:2001:db8::7'):
        cert, key = self.path / 'cert.pem', self.path / 'key.pem'
        args = [self.openssl, 'req', '-x509', '-newkey', 'rsa:2048', '-nodes',
                '-keyout', str(key), '-out', str(cert), '-days', '2', '-subj', subject]
        if san:
            args += ['-addext', f'subjectAltName={san}']
        subprocess.run(args, check=True, capture_output=True)
        self.env['TLS_CERT'] = str(cert)
        return cert, key

    def test_certificate_metadata(self):
        cert, _ = self.certificate()
        expected_expiry = subprocess.check_output(
            [self.openssl, 'x509', '-in', str(cert), '-noout', '-enddate'], text=True).strip().split('=', 1)[1]
        result = self.run_tool('certinfo', 'example.test')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stderr, '')
        self.assertIn('CN: example.test\n', result.stdout)
        self.assertIn('Issuer: ', result.stdout)
        self.assertIn('Fixture', result.stdout)
        self.assertIn(f'Expires: {expected_expiry}\n', result.stdout)
        for san in ('DNS:example.test', 'DNS:alt.test', 'IP:192.0.2.7', 'IP:2001:DB8:0:0:0:0:0:7'):
            self.assertIn(san + '\n', result.stdout)

    def test_missing_parse_dependencies(self):
        self.certificate()
        for missing in ('sed', 'tr'):
            with self.subTest(missing=missing):
                isolated = self.path / f'without-{missing}'
                isolated.mkdir()
                for dep in ('dirname', 'timeout', 'mktemp', 'cat', 'rm', 'sed', 'tr'):
                    if dep != missing:
                        executable = shutil.which(dep)
                        assert executable is not None
                        (isolated / dep).symlink_to(executable)
                (isolated / 'openssl').symlink_to(self.path / 'openssl')
                result = self.run_tool('certinfo', 'example.test',
                                       env=dict(self.env, PATH=str(isolated)))
                self.assertEqual(result.returncode, 1, result.stderr)
                self.assertEqual(result.stdout, '')
                self.assertIn(f'missing dependency: {missing}', result.stderr)

    def test_failed_metadata_text_processing(self):
        self.certificate()
        real_sed = shutil.which('sed')
        assert real_sed is not None
        for stage in ('cn-split', 'cn-select', 'san-select', 'san-split'):
            with self.subTest(stage=stage):
                dep = 'tr' if stage == 'san-split' else 'sed'
                stub = self.path / dep
                stub.write_text('''#!/bin/bash
case "$FAIL_STAGE:$*" in
  cn-split:*'s/ + /'*|cn-select:*'CN *='*|san-select:*'DNS:'*|san-split:*)
    printf 'fixture text processing failure\\n' >&2
    exit 9 ;;
esac
exec "$REAL_SED" "$@"
''')
                stub.chmod(0o755)
                try:
                    result = self.run_tool('certinfo', 'example.test',
                                           env=dict(self.env, FAIL_STAGE=stage, REAL_SED=real_sed))
                    self.assertEqual(result.returncode, 1, result.stderr)
                    self.assertEqual(result.stdout, '')
                    self.assertIn('cannot parse certificate', result.stderr)
                finally:
                    stub.unlink()

    def test_multivalued_subject_rdn(self):
        self.certificate(subject='/O=Fixture+CN=example.test/OU=After CN')
        result = self.run_tool('certinfo', 'example.test')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn('CN: example.test\n', result.stdout)

    def test_dns_only_and_escaped_subject(self):
        self.certificate(subject='/O=Literal \\+ CN=decoy/OU=After/CN=real.test', san='DNS:real.test')
        result = self.run_tool('certinfo', 'example.test')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stderr, '')
        self.assertIn('CN: real.test\n', result.stdout)
        self.assertNotIn('CN: decoy', result.stdout)
        self.assertIn('SAN(s):\nDNS:real.test\n', result.stdout)

    def test_distinct_issuer(self):
        ca, key = self.certificate()
        csr, leaf = self.path / 'leaf.csr', self.path / 'leaf.pem'
        subprocess.run([self.openssl, 'req', '-new', '-key', str(key), '-subj',
                        '/O=Leaf/CN=leaf.test', '-out', str(csr)], check=True, capture_output=True)
        subprocess.run([self.openssl, 'x509', '-req', '-in', str(csr), '-CA', str(ca),
                        '-CAkey', str(key), '-set_serial', '2', '-days', '2', '-out', str(leaf)],
                       check=True, capture_output=True)
        result = self.run_tool('certinfo', 'example.test', env=dict(self.env, TLS_CERT=str(leaf)))
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn('CN: leaf.test\n', result.stdout)
        self.assertIn('Issuer: CN=example.test,OU=Testing,O=Fixture\\, Inc.\n', result.stdout)

    def test_absent_cn_and_sans(self):
        self.certificate(subject='/O=No common name', san=None)
        result = self.run_tool('certinfo', 'example.test')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stderr, '')
        self.assertIn('CN: (none)\n', result.stdout)
        self.assertIn('SAN(s):\n(none)\n', result.stdout)

    def test_ip_only_sans(self):
        self.certificate(subject='/O=IP only', san='IP:192.0.2.7,IP:2001:db8::7')
        result = self.run_tool('certinfo', '[::1]')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn('CN: (none)\n', result.stdout)
        self.assertIn('IP:192.0.2.7\n', result.stdout)
        self.assertIn('IP:2001:DB8:0:0:0:0:0:7\n', result.stdout)

    def test_ced_days_and_safe_color(self):
        self.certificate()
        for env in (self.env, dict(self.env, TERM='xterm', NO_COLOR='')):
            result = self.run_tool('ced', 'example.test', env=env)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(result.stderr, '')
            self.assertNotIn('\x1b', result.stdout)
            self.assertRegex(result.stdout, r'EXPIRES: .+ GMT\nDAYS REMAINING: [12]\n')
        for no_color in (False, True):
            master, slave = pty.openpty()
            try:
                env = dict(self.env, TERM='xterm')
                if not no_color:
                    env.pop('NO_COLOR', None)
                result = subprocess.run([str(ROOT / 'linux' / 'ced'), 'example.test'],
                                        env=env, stdout=slave, stderr=subprocess.PIPE, timeout=8)
                self.assertEqual(result.returncode, 0, result.stderr)
                output = os.read(master, 8192).decode()
                self.assertEqual('\x1b[31m' in output, not no_color)
            finally:
                os.close(master)
                os.close(slave)

    def test_usage_before_transport(self):
        for tool in ('certinfo', 'ced'):
            result = self.run_tool(tool, '--help')
            self.assertEqual(result.returncode, 0)
            self.assertIn('--timeout', result.stdout)
            self.assertIn('--servername', result.stdout)
            self.assertEqual(result.stderr, '')
            for args in ((), ('a', 'b'), ('--bad',), ('a:0',), ('a:65536',),
                         ('a:abc',), ('a:',), ('[::1',), ('::1',), ('-x',),
                         ('a b',), ('--timeout', '0', 'a'), ('--timeout', 'NaN', 'a'),
                         ('--timeout',), ('--servername',), ('--servername', '-x', 'a')):
                with self.subTest(tool=tool, args=args):
                    result = self.run_tool(tool, *args)
                    self.assertEqual(result.returncode, 2)
                    self.assertEqual(result.stdout, '')
                    self.assertIn('Usage:', result.stderr)
            self.assertFalse((self.path / 'args').exists())

    def test_double_dash_rejects_second_target_before_transport(self):
        self.certificate()
        for tool in ('certinfo', 'ced'):
            with self.subTest(tool=tool):
                (self.path / 'args').unlink(missing_ok=True)
                result = self.run_tool(tool, 'first.test', '--', 'second.test')
                self.assertEqual(result.returncode, 2, result.stderr)
                self.assertEqual(result.stdout, '')
                self.assertIn('expected one target', result.stderr)
                self.assertFalse((self.path / 'args').exists())

    def test_double_dash_accepts_single_target(self):
        self.certificate()
        for tool in ('certinfo', 'ced'):
            with self.subTest(tool=tool):
                result = self.run_tool(tool, '--', 'example.test')
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertEqual(result.stderr, '')
                args = (self.path / 'args').read_text().splitlines()
                self.assertEqual(args[args.index('-connect') + 1], 'example.test:443')

    def test_invalid_ipv6_is_usage_error(self):
        for tool in ('certinfo', 'ced'):
            for address in ('[:::]', '[1:2:3]', '[1:2:3:4:5:6:7:8:9]',
                            '[1::2::3]', '[12345::1]', '[::ffff:999.0.0.1]'):
                with self.subTest(tool=tool, address=address):
                    result = self.run_tool(tool, address)
                    self.assertEqual(result.returncode, 2)
                    self.assertFalse((self.path / 'args').exists())

    def test_transport_address_and_sni(self):
        for tool in ('certinfo', 'ced'):
            for target, extra, connect, sni in (
                ('[2001:db8::1]:8443', ['--servername', 'example.test'], '[2001:db8::1]:8443', 'example.test'),
                ('[::1]', [], '[::1]:443', None),
                ('[::]', [], '[::]:443', None),
                ('[1:2:3:4:5:6:7:8]:65535', [], '[1:2:3:4:5:6:7:8]:65535', None),
                ('[::ffff:192.0.2.7]', [], '[::ffff:192.0.2.7]:443', None),
                ('[2001:db8::]', [], '[2001:db8::]:443', None),
                ('127.0.0.1:00443', [], '127.0.0.1:443', None),
                ('example.test', [], 'example.test:443', 'example.test'),
            ):
                with self.subTest(tool=tool, target=target):
                    self.run_tool(tool, *extra, target)
                    args = (self.path / 'args').read_text().splitlines()
                    self.assertEqual(args[args.index('-connect') + 1], connect)
                    if sni:
                        self.assertEqual(args[args.index('-servername') + 1], sni)
                    else:
                        self.assertIn('-noservername', args)

    def test_real_loopback_timeout(self):
        # Listening socket deliberately never speaks TLS; no external endpoints.
        with socket.socket() as listener:
            listener.bind(('127.0.0.1', 0))
            listener.listen(8)
            env = dict(self.env, PATH=os.environ['PATH'])
            for tool in ('certinfo', 'ced'):
                start = time.monotonic()
                result = self.run_tool(tool, '--timeout', '1',
                                       f'127.0.0.1:{listener.getsockname()[1]}', env=env)
                self.assertEqual(result.returncode, 1)
                self.assertLess(time.monotonic() - start, 4)
                self.assertIn('timed out', result.stderr)
                self.assertEqual(result.stdout, '')

    def test_real_loopback_self_signed_and_expired(self):
        cert, key = self.certificate()
        for expired in (False, True):
            if expired:
                old = self.path / 'expired.pem'
                subprocess.run([self.openssl, 'x509', '-in', str(cert), '-signkey', str(key),
                                '-days', '0', '-out', str(old)], check=True, capture_output=True)
                time.sleep(1.1)  # A zero-day certificate is now definitely expired.
                cert = old
            context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
            context.load_cert_chain(str(cert), str(key))
            names = []
            context.set_servername_callback(lambda sock, name, ctx: names.append(name))
            errors = []
            with socket.socket() as listener:
                listener.bind(('127.0.0.1', 0))
                listener.listen(2)
                listener.settimeout(6)

                def serve():
                    try:
                        for _ in range(2):
                            raw, _ = listener.accept()
                            raw.settimeout(5)
                            with context.wrap_socket(raw, server_side=True) as peer:
                                peer.recv(1)
                                try:
                                    peer.unwrap().close()
                                except ssl.SSLError:
                                    pass  # s_client may already have closed its socket.
                    except Exception as error:
                        errors.append(error)

                worker = threading.Thread(target=serve, daemon=True)
                worker.start()
                try:
                    for tool in ('certinfo', 'ced'):
                        result = self.run_tool(tool, '--timeout', '3', '--servername', 'override.test',
                                               f'127.0.0.1:{listener.getsockname()[1]}',
                                               env=dict(self.env, PATH=os.environ['PATH']))
                        self.assertEqual(result.returncode, 0, result.stderr)
                        self.assertEqual(result.stderr, '')
                        if tool == 'certinfo':
                            self.assertIn('CN: example.test\n', result.stdout)
                        elif expired:
                            self.assertRegex(result.stdout, r'DAYS REMAINING: -[12]\n')
                finally:
                    worker.join(7)
                self.assertFalse(worker.is_alive())
                self.assertEqual(errors, [])
                self.assertEqual(names, ['override.test', 'override.test'])

    def test_bad_certificate_data(self):
        cert = self.path / 'invalid.pem'
        for content in ('', 'not a certificate', '-----BEGIN CERTIFICATE-----\ntruncated\n'):
            cert.write_text(content)
            for tool in ('certinfo', 'ced'):
                result = self.run_tool(tool, 'example.test', env=dict(self.env, TLS_CERT=str(cert)))
                self.assertEqual(result.returncode, 1)
                self.assertEqual(result.stdout, '')
                self.assertIn('certificate', result.stderr.lower())

    def test_connection_failure(self):
        for tool in ('certinfo', 'ced'):
            with self.subTest(tool=tool):
                result = self.run_tool(tool, 'example.test')
                self.assertEqual(result.returncode, 1)
                self.assertEqual(result.stdout, '')
                self.assertIn('fixture connection refused', result.stderr)


if __name__ == '__main__':
    unittest.main()
