"""Offline mcm integration tests: no real credentials or clusters."""
from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import signal
import shlex
import unittest

import yaml

ROOT = Path(__file__).resolve().parents[1]
MCM = ROOT / "openshift/mcm"
FAKE_OC = r'''#!/usr/bin/env python3
import json, os, sys
from pathlib import Path
import yaml
args = sys.argv[1:]
if os.environ.get('OC_SLEEP') == args[0]:
    import time
    time.sleep(5)
p = Path(os.environ['KUBECONFIG'])
data = yaml.safe_load(p.read_text())
with open(os.environ['OC_LOG'], 'a') as f:
    f.write(json.dumps({'args': args, 'config': data}) + '\n')
if args[0] == 'login':
    import termios
    assert '-p' not in args and not any(a.startswith('--password') for a in args)
    assert not any('synthetic secret' in value for value in os.environ.values())
    assert not any('synthetic secret' in value for value in args)
    if os.environ.get('LOGIN_ERROR'):
        print(os.environ['LOGIN_ERROR'])
        sys.exit(1)
    assert os.isatty(0)
    assert not (termios.tcgetattr(0)[3] & termios.ECHO)
    print('Password: ', end='', flush=True)
    password = sys.stdin.readline().rstrip('\r\n')
    assert password == ' synthetic secret with spaces '
    if os.environ.get('REJECT_PASSWORD'):
        print('Unauthorized ' + password)
        sys.exit(1)
    data['users'][0]['user'] = {'token': os.environ.get('LOGIN_IDENTITY', 'alice')}
    if os.environ.get('LOGIN_SERVER'):
        data['clusters'][0]['cluster']['server'] = os.environ['LOGIN_SERVER']
    p.write_text(yaml.safe_dump(data))
    sys.exit(0)
if args[0] == 'whoami':
    if os.environ.get('WHOAMI_ERROR'):
        print(os.environ['WHOAMI_ERROR'], file=sys.stderr)
        sys.exit(1)
    token = data['users'][0]['user'].get('token')
    if token == 'expired':
        token = None
    print(token or 'Unauthorized')
    sys.exit(0 if token else 1)
if args[0] == 'logout':
    sys.exit(int(os.environ.get('LOGOUT_RC', '0')))
'''


class McmTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.home = Path(self.temp.name)
        self.config = self.home / '.mcm.yaml'
        self.log = self.home / 'oc.log'
        oc = self.home / 'oc'
        oc.write_text(FAKE_OC)
        oc.chmod(0o700)
        self.env = dict(os.environ, HOME=str(self.home),
                        PATH=str(self.home) + os.pathsep + os.environ['PATH'],
                        OC_LOG=str(self.log))
        self.data = {'credential_groups': {'staff': 'alice'}, 'clusters': []}
        self.add_cluster('a')

    def add_cluster(self, name, token='alice', server=None):
        server = server or 'https://' + name + '.invalid:6443'
        kube = self.home / (name + '.yaml')
        kube.write_text(yaml.safe_dump({
            'apiVersion': 'v1', 'kind': 'Config', 'current-context': name,
            'clusters': [{'name': name, 'cluster': {'server': server}}],
            'contexts': [{'name': name, 'context': {'cluster': name, 'user': name}}],
            'users': [{'name': name, 'user': {'token': token}}]}))
        self.data['clusters'].append({'name': name, 'server': server,
                                     'kubeconfig': str(kube), 'credential_group': 'staff'})
        self.save()
        return kube

    def save(self):
        self.config.write_text(yaml.safe_dump(self.data))

    def run_mcm(self, *args, **kwargs):
        return subprocess.run([sys.executable, str(MCM), *args], env=self.env,
                              capture_output=True, text=True, timeout=15, **kwargs)

    def calls(self):
        return [json.loads(line) for line in self.log.read_text().splitlines()] if self.log.exists() else []

    def prompted(self, *args):
        # Patch only the human prompt; real oc child uses a real private PTY.
        script = "import getpass,runpy,sys; getpass.getpass=lambda prompt: (print('HUMAN PROMPT '+prompt, file=sys.stderr) or ' synthetic secret with spaces '); sys.argv=sys.argv[1:]; runpy.run_path(sys.argv[0],run_name='__main__')"
        return subprocess.run([sys.executable, '-c', script, str(MCM), *args],
                              env=self.env, capture_output=True, text=True, timeout=15)

    def test_auto_login_prompts_once_for_explicit_group_using_private_pty(self):
        self.add_cluster('b', token='')
        self.add_cluster('c', token='')
        result = self.prompted('exec', '--command', 'printf EXECUTED')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stderr.count('HUMAN PROMPT'), 1)
        self.assertEqual(result.stdout.count('EXECUTED'), 3)
        self.assertEqual(len([c for c in self.calls() if c['args'][0] == 'login']), 2)
        for output in (result.stdout, result.stderr, self.log.read_text(), self.config.read_text()):
            self.assertNotIn('synthetic secret', output)
        again = self.prompted('exec', '--command', 'true')
        self.assertEqual(again.returncode, 0, again.stderr)
        self.assertNotIn('HUMAN PROMPT', again.stderr)

    def test_login_network_or_tls_failure_never_prompts(self):
        self.add_cluster('b', token='')
        for error in ('connection refused', 'x509: unknown authority'):
            self.env['LOGIN_ERROR'] = error
            result = self.prompted('exec', '--command', 'printf EXECUTED')
            self.assertEqual(result.returncode, 1)
            self.assertNotIn('HUMAN PROMPT', result.stderr)
            self.assertNotIn('EXECUTED', result.stdout)

    def test_rejected_password_blocks_rest_of_group_without_leaking(self):
        self.add_cluster('b', token='')
        self.add_cluster('c', token='')
        self.env['REJECT_PASSWORD'] = '1'
        result = self.prompted('exec', '--command', 'printf EXECUTED')
        self.assertEqual(result.returncode, 1)
        self.assertEqual(len([c for c in self.calls() if c['args'][0] == 'login']), 1)
        self.assertNotIn('synthetic secret', result.stdout + result.stderr)

    def test_login_missing_kubeconfig_selection_ca_and_config_override(self):
        kube = self.add_cluster('b')
        kube.unlink()
        self.data['clusters'][1]['certificate_authority'] = 'ca.crt'
        (self.home / 'ca.crt').write_text('fixture CA')
        self.save()
        alternate = self.home / 'registrations.yaml'
        self.config.rename(alternate)
        result = self.prompted('--config', str(alternate), 'login', '--cluster', 'b')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stderr.count('HUMAN PROMPT'), 1)
        self.assertTrue(kube.exists())
        self.assertEqual(self.calls()[0]['config']['clusters'][0]['cluster']['certificate-authority'], str(self.home / 'ca.crt'))
        self.assertTrue(all('https://b.invalid' in str(c['config']) for c in self.calls()))

    def test_continue_on_error_runs_only_healthy_and_returns_failure(self):
        self.add_cluster('b', token='mallory')
        result = self.run_mcm('exec', '--command', 'printf EXECUTED', '--continue-on-error')
        self.assertEqual(result.returncode, 1)
        self.assertEqual(result.stdout.count('EXECUTED'), 1)
        self.assertIn('identity', result.stderr)

    def test_schema_and_path_errors_fail_before_oc_without_traceback(self):
        baseline = json.loads(json.dumps(self.data))
        cases = [[], {'clusters': None}, {'credential_groups': [], 'clusters': []}]
        for key, value in [('name', ''), ('server', 'http://a.invalid'), ('kubeconfig', ''),
                           ('kubeconfig', 'one:two'), ('credential_group', 'missing')]:
            data = json.loads(json.dumps(baseline))
            data['clusters'][0][key] = value
            cases.append(data)
        for data in cases:
            self.config.write_text(yaml.safe_dump(data))
            result = self.run_mcm('exec', '--command', 'true')
            self.assertNotEqual(result.returncode, 0, repr(data))
            self.assertNotIn('Traceback', result.stderr, repr(data))
        self.assertEqual(self.calls(), [])

    def test_shared_or_dangerous_kubeconfig_rejected(self):
        self.add_cluster('b')
        original = self.data['clusters'][1]['kubeconfig']
        link = self.home / 'link'
        link.symlink_to(self.home / 'a.yaml')
        for path in (self.data['clusters'][0]['kubeconfig'], str(link), str(self.config), '/dev/null'):
            self.data['clusters'][1]['kubeconfig'] = path
            self.save()
            result = self.run_mcm('login')
            self.assertNotEqual(result.returncode, 0, path)
            self.assertNotIn('Traceback', result.stderr)
        self.data['clusters'][1]['kubeconfig'] = original
        self.assertEqual(self.calls(), [])

    def test_empty_or_unknown_selection_fails(self):
        result = self.run_mcm('login', '--cluster', 'absent')
        self.assertNotEqual(result.returncode, 0)
        self.data['clusters'] = []
        self.save()
        result = self.run_mcm('exec', '--command', 'true')
        self.assertNotEqual(result.returncode, 0)

    def test_crud_legacy_isolation_and_concurrent_atomic_adds(self):
        self.config.unlink()
        processes = [subprocess.Popen([sys.executable, str(MCM), 'add', 'c'+str(i),
                                      'https://c'+str(i)+'.invalid', 'kube'+str(i), '--user', 'alice'],
                                     env=self.env, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
                     for i in range(6)]
        outcomes = [(process, process.communicate(timeout=15)) for process in processes]
        for process, (out, err) in outcomes:
            self.assertEqual(process.returncode, 0, out + err)
        data = yaml.safe_load(self.config.read_text())
        self.assertEqual(len(data['clusters']), 6)
        self.assertEqual(len({c['credential_group'] for c in data['clusters']}), 6)
        self.assertEqual(self.config.stat().st_mode & 0o777, 0o600)
        listed = self.run_mcm('list')
        self.assertEqual(listed.returncode, 0, listed.stderr)
        self.assertIn('c5', listed.stdout)
        result = self.run_mcm('remove', 'c5')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(len(yaml.safe_load(self.config.read_text())['clusters']), 5)
        self.assertNotEqual(self.run_mcm('remove', 'absent').returncode, 0)

    def test_legacy_usernames_do_not_share_password_groups(self):
        self.add_cluster('b', token='')
        for cluster in self.data['clusters']:
            cluster.pop('credential_group')
            cluster['username'] = 'alice'
            Path(cluster['kubeconfig']).unlink()
        self.data.pop('credential_groups')
        self.save()
        result = self.prompted('login')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stderr.count('HUMAN PROMPT'), 2)

    def test_timeout_kills_shell_descendants_and_keeps_partial_output(self):
        marker = self.home / 'late'
        command = 'printf partial; (sleep 0.6; touch ' + shlex.quote(str(marker)) + ') & wait'
        result = self.run_mcm('exec', '--command', command, '--timeout', '0.2')
        self.assertEqual(result.returncode, 1, result.stderr)
        self.assertIn('partial', result.stdout)
        self.assertIn('timed out', result.stderr)
        time.sleep(0.7)
        self.assertFalse(marker.exists())

    def test_every_oc_phase_has_request_and_process_timeout(self):
        for phase in ('whoami', 'login'):
            self.env['OC_SLEEP'] = phase
            (self.home / 'a.yaml').unlink(missing_ok=True)
            started = time.monotonic()
            result = self.prompted('login', '--timeout', '0.2')
            self.assertEqual(result.returncode, 1)
            self.assertLess(time.monotonic() - started, 2)
            self.assertIn('timed out', result.stderr)
            self.assertNotIn('HUMAN PROMPT', result.stderr)
        self.env.pop('OC_SLEEP')
        result = self.prompted('login', '--timeout', '2')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertTrue(all(any(a.startswith('--request-timeout=') for a in c['args']) for c in self.calls()))

    def test_logout_failure_retains_session_success_clears_token(self):
        original = (self.home / 'a.yaml').read_text()
        self.env['LOGOUT_RC'] = '7'
        result = self.run_mcm('logout')
        self.assertEqual(result.returncode, 1, result.stderr)
        self.assertNotIn('logged out', result.stdout)
        self.assertEqual(yaml.safe_load((self.home / 'a.yaml').read_text())['users'][0]['user']['token'], 'alice')
        self.env['LOGOUT_RC'] = '0'
        result = self.run_mcm('logout')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn('logged out', result.stdout)
        self.assertNotEqual((self.home / 'a.yaml').read_text(), original)
        self.assertEqual(yaml.safe_load((self.home / 'a.yaml').read_text())['users'][0]['user'], {})
        self.assertTrue(all(any(a.startswith('--request-timeout=') for a in c['args']) for c in self.calls()))

    def test_logout_timeout_never_reports_success(self):
        self.env['OC_SLEEP'] = 'logout'
        result = self.run_mcm('logout', '--timeout', '0.2')
        self.assertEqual(result.returncode, 1, result.stderr)
        self.assertIn('timed out', result.stderr)
        self.assertNotIn('logged out', result.stdout)

    def test_table_keeps_multiline_output_and_stderr(self):
        result = self.run_mcm('exec', '--command', "printf 'one\\ntwo\\n'; printf 'bad\\nnews\\n' >&2; exit 4", '--output', 'table')
        self.assertEqual(result.returncode, 1, result.stderr)
        for value in ('one', 'two', 'ERROR', '4'):
            self.assertIn(value, result.stdout)
        for value in ('bad', 'news'):
            self.assertIn(value, result.stderr)

    def test_workers_run_concurrently_and_reject_nonpositive(self):
        self.add_cluster('b')
        barrier = self.home / 'barrier'
        script = ("import os,pathlib,time; p=pathlib.Path(" + repr(str(barrier)) + "); "
                  "p.mkdir(exist_ok=True); (p/str(os.getpid())).touch(); "
                  "deadline=time.monotonic()+2\n"
                  "while len(list(p.iterdir())) < 2 and time.monotonic()<deadline: time.sleep(.02)\n"
                  "assert len(list(p.iterdir())) == 2")
        result = self.run_mcm('exec', '--workers', '2', '--command', shlex.quote(sys.executable) + ' -c ' + shlex.quote(script))
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertLess(result.stdout.index('== a'), result.stdout.index('== b'))
        for workers in ('0', '-1'):
            result = self.run_mcm('exec', '--workers', workers, '--command', 'true')
            self.assertNotEqual(result.returncode, 0)
            self.assertNotIn('Traceback', result.stderr)

    def test_interrupt_kills_parallel_command_descendants(self):
        self.add_cluster('b')
        started = self.home / 'started'
        late = self.home / 'late'
        command = 'touch ' + shlex.quote(str(started)) + '; (sleep 1; touch ' + shlex.quote(str(late)) + ') & wait'
        process = subprocess.Popen([sys.executable, str(MCM), 'exec', '--workers', '2', '--command', command],
                                   env=self.env, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        deadline = time.monotonic() + 4
        while not started.exists() and process.poll() is None and time.monotonic() < deadline:
            time.sleep(0.02)
        process.send_signal(signal.SIGINT)
        out, err = process.communicate(timeout=4)
        self.assertTrue(started.exists(), err)
        self.assertEqual(process.returncode, 130, out + err)
        self.assertNotIn('Traceback', err)
        time.sleep(1.1)
        self.assertFalse(late.exists())

    def test_malformed_kubeconfig_and_credential_plugins_are_rejected(self):
        path = self.home / 'a.yaml'
        baseline = yaml.safe_load(path.read_text())
        cases = [[], {}, {'contexts': None}]
        plugin = json.loads(json.dumps(baseline))
        plugin['users'][0]['user']['exec'] = {'command': 'sh', 'args': ['-c', 'touch SHOULD_NOT_RUN']}
        cases.append(plugin)
        for data in cases:
            path.write_text(yaml.safe_dump(data))
            result = self.run_mcm('login')
            self.assertEqual(result.returncode, 1)
            self.assertNotIn('Traceback', result.stderr)
        self.assertEqual(self.calls(), [])

    def test_invalid_yaml_does_not_echo_source_or_traceback(self):
        self.config.write_text('secret_value: [never-echo-this-source')
        result = self.run_mcm('list')
        self.assertEqual(result.returncode, 1)
        self.assertNotIn('never-echo-this-source', result.stderr)
        self.assertNotIn('Traceback', result.stderr)

    def test_login_wrong_identity_blocks_group_and_does_not_persist_token(self):
        self.add_cluster('b', token='')
        self.add_cluster('c', token='')
        self.env['LOGIN_IDENTITY'] = 'mallory'
        result = self.prompted('login')
        self.assertEqual(result.returncode, 1, result.stderr)
        self.assertEqual(len([c for c in self.calls() if c['args'][0] == 'login']), 1)
        self.assertNotIn('mallory', (self.home / 'b.yaml').read_text())

    def test_namespace_survives_isolation(self):
        path = self.home / 'a.yaml'
        data = yaml.safe_load(path.read_text())
        data['contexts'][0]['context']['namespace'] = 'team-project'
        path.write_text(yaml.safe_dump(data))
        result = self.run_mcm('login')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.calls()[0]['config']['contexts'][0]['context'].get('namespace'), 'team-project')

    def test_noninteractive_password_input_is_not_accepted(self):
        (self.home / 'a.yaml').unlink()
        result = self.run_mcm('login', input=' synthetic secret with spaces \n', start_new_session=True)
        self.assertEqual(result.returncode, 1, result.stderr)
        self.assertNotIn('Traceback', result.stderr)
        self.assertNotIn('synthetic secret', result.stdout + result.stderr)
        self.assertIn('terminal', result.stderr.lower())

    def test_login_timeout_bounds_human_prompt(self):
        (self.home / 'a.yaml').unlink()
        script = "import getpass,runpy,sys,time; getpass.getpass=lambda prompt: (time.sleep(2) or ' synthetic secret with spaces '); sys.argv=sys.argv[1:]; runpy.run_path(sys.argv[0],run_name='__main__')"
        started = time.monotonic()
        result = subprocess.run([sys.executable, '-c', script, str(MCM), 'login', '--timeout', '0.2'],
                                env=self.env, capture_output=True, text=True, timeout=4)
        self.assertEqual(result.returncode, 1, result.stderr)
        self.assertLess(time.monotonic() - started, 1)
        self.assertNotIn('Traceback', result.stderr)

    def test_insecure_override_does_not_mix_ca_and_insecure_client_options(self):
        (self.home / 'ca.crt').write_text('fixture CA')
        self.data['clusters'][0]['certificate_authority'] = 'ca.crt'
        self.save()
        result = self.run_mcm('login', '--insecure-skip-tls-verify')
        self.assertEqual(result.returncode, 0, result.stderr)
        target = self.calls()[0]['config']['clusters'][0]['cluster']
        self.assertTrue(target['insecure-skip-tls-verify'])
        self.assertNotIn('certificate-authority', target)
        saved = yaml.safe_load((self.home / 'a.yaml').read_text())['clusters'][0]['cluster']
        self.assertEqual(saved['certificate-authority'], str(self.home / 'ca.crt'))
        self.assertNotIn('insecure-skip-tls-verify', saved)

    def test_expired_token_refresh_does_not_reauthenticate_valid_target(self):
        self.add_cluster('b', token='expired')
        result = self.prompted('login')
        self.assertEqual(result.returncode, 0, result.stderr)
        logins = [call for call in self.calls() if call['args'][0] == 'login']
        self.assertEqual(len(logins), 1)
        self.assertIn('https://b.invalid:6443', logins[0]['args'])

    def test_preflight_network_tls_and_missing_oc_never_prompt(self):
        for error in ('connection refused', 'x509: unknown authority', 'Forbidden'):
            self.env['WHOAMI_ERROR'] = error
            result = self.prompted('exec', '--command', 'printf EXECUTED')
            self.assertEqual(result.returncode, 1)
            self.assertNotIn('HUMAN PROMPT', result.stderr)
            self.assertNotIn('EXECUTED', result.stdout)
        self.assertFalse(any(call['args'][0] == 'login' for call in self.calls()))
        self.env.pop('WHOAMI_ERROR')
        self.env['PATH'] = ''
        result = self.prompted('login')
        self.assertEqual(result.returncode, 1)
        self.assertNotIn('HUMAN PROMPT', result.stderr)
        self.assertNotIn('Traceback', result.stderr)

    def test_selection_deduplicates_and_ignores_ambient_kubeconfig(self):
        self.add_cluster('b', token='mallory')
        self.env['KUBECONFIG'] = '/do/not/read:/or/write'
        result = self.run_mcm('exec', '--cluster', 'a', '--cluster', 'a', '--command', 'printf EXECUTED')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout.count('EXECUTED'), 1)
        self.assertEqual(len(self.calls()), 1)

    def test_add_explicit_group_reuses_only_declared_group(self):
        result = self.run_mcm('add', 'b', 'https://b.invalid', 'b.yaml', '--credential-group', 'staff')
        self.assertEqual(result.returncode, 0, result.stderr)
        data = yaml.safe_load(self.config.read_text())
        self.assertEqual(data['clusters'][1]['credential_group'], 'staff')
        before = self.config.read_text()
        result = self.run_mcm('add', 'c', 'https://c.invalid', 'c.yaml', '--credential-group', 'staff', '--user', 'mallory')
        self.assertEqual(result.returncode, 1)
        self.assertEqual(self.config.read_text(), before)

    def test_login_server_is_checked_before_identity_request(self):
        (self.home / 'a.yaml').unlink()
        self.env['LOGIN_SERVER'] = 'https://wrong.invalid'
        result = self.prompted('login')
        self.assertEqual(result.returncode, 1, result.stderr)
        self.assertIn('server', result.stderr)
        self.assertFalse(any(c['args'][0] == 'whoami' and 'wrong.invalid' in str(c['config']) for c in self.calls()))

    def test_insecure_login_preserves_cached_ca(self):
        path = self.home / 'a.yaml'
        data = yaml.safe_load(path.read_text())
        data['users'][0]['user'] = {}
        data['clusters'][0]['cluster']['certificate-authority-data'] = 'Zml4dHVyZQ=='
        path.write_text(yaml.safe_dump(data))
        result = self.prompted('login', '--insecure-skip-tls-verify')
        self.assertEqual(result.returncode, 0, result.stderr)
        saved = yaml.safe_load(path.read_text())['clusters'][0]['cluster']
        self.assertEqual(saved.get('certificate-authority-data'), 'Zml4dHVyZQ==')
        self.assertNotIn('insecure-skip-tls-verify', saved)

    def test_malformed_ca_and_unknown_home_paths_are_concise_errors(self):
        path = self.home / 'a.yaml'
        baseline = yaml.safe_load(path.read_text())
        for key, value in [('certificate-authority', 7), ('certificate-authority-data', []), ('token', False)]:
            data = json.loads(json.dumps(baseline))
            target = data['users'][0]['user'] if key == 'token' else data['clusters'][0]['cluster']
            target[key] = value
            path.write_text(yaml.safe_dump(data))
            result = self.run_mcm('login')
            self.assertEqual(result.returncode, 1)
            self.assertNotIn('Traceback', result.stderr)
        self.data['clusters'][0]['kubeconfig'] = '~mcm-no-such-user-fixture/config'
        self.save()
        result = self.run_mcm('list')
        self.assertEqual(result.returncode, 1)
        self.assertNotIn('Traceback', result.stderr)
        self.assertEqual(self.calls(), [])

    def test_server_mismatch_never_runs_oc(self):
        self.data['clusters'][0]['server'] = 'https://other.invalid'
        self.save()
        result = self.run_mcm('exec', '--command', 'printf EXECUTED')
        self.assertEqual(result.returncode, 1)
        self.assertNotIn('EXECUTED', result.stdout)
        self.assertIn('server', result.stderr)
        self.assertEqual(self.calls(), [])

    def test_cached_insecure_is_removed_and_override_is_ephemeral(self):
        path = self.home / 'a.yaml'
        data = yaml.safe_load(path.read_text())
        data['clusters'][0]['cluster']['insecure-skip-tls-verify'] = True
        path.write_text(yaml.safe_dump(data))
        for flags, expected in [((), False), (('--insecure-skip-tls-verify',), True)]:
            result = self.run_mcm('exec', '--command', 'printf safe', *flags)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(self.calls()[-1]['config']['clusters'][0]['cluster'].get('insecure-skip-tls-verify', False), expected)
        self.assertIn('a', result.stderr)
        self.assertNotIn('insecure-skip-tls-verify', path.read_text())

    def test_preflight_rejects_wrong_identity_before_any_execution(self):
        self.add_cluster('b', token='mallory')
        result = self.run_mcm('exec', '--command', 'printf EXECUTED')
        self.assertEqual(result.returncode, 1)
        self.assertNotIn('EXECUTED', result.stdout)
        self.assertIn('identity', result.stderr)

    def test_valid_session_exec_keeps_both_streams_and_failure(self):
        result = self.run_mcm('exec', '--command', 'printf partial; printf denied >&2; exit 7')
        self.assertEqual(result.returncode, 1, result.stderr)
        self.assertIn('partial', result.stdout)
        self.assertIn('denied', result.stderr)
        self.assertEqual(self.calls()[0]['args'][0], 'whoami')


if __name__ == '__main__':
    unittest.main()
