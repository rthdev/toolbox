"""Optional real-oc compatibility checks; never contact an external cluster.

Run with a locally verified binary, for example:
MCM_REAL_OC=/absolute/path/to/oc python -m unittest discover -s tests -p test_mcm_real_oc.py -v
The ordinary suite skips these checks; no binary is downloaded by a test.
"""
from __future__ import annotations

import errno
import os
from pathlib import Path
import pty
import re
import select
import signal
import subprocess
import sys
import tempfile
import termios
import time
import unittest

import yaml

from mcm_oauth_fixture import OAuthFixture, PASSWORD, TOKEN, USERNAME

SCRIPT = Path(__file__).resolve().parents[1] / "openshift" / "mcm"
REAL_OC = os.environ.get("MCM_REAL_OC")


@unittest.skipUnless(REAL_OC, "set MCM_REAL_OC to run loopback real-oc compatibility checks")
class RealOcTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="mcm-real-")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.bin = self.root / "bin"
        self.bin.mkdir()
        assert REAL_OC is not None
        binary = Path(REAL_OC).resolve(strict=True)
        (self.bin / "oc").symlink_to(binary)
        self.config = self.root / "mcm.yaml"
        self.environment = dict(os.environ, HOME=str(self.root),
                                PATH=str(self.bin) + os.pathsep + os.environ["PATH"],
                                KUBECONFIG=str(self.root / "ambient-unused.yaml"))

    def register(self, fixtures, trusted=True):
        entries = []
        for index, fixture in enumerate(fixtures):
            entry = {"name": "cluster-" + str(index), "server": fixture.url,
                     "credential_group": "corporate",
                     "kubeconfig": str(self.root / ("kube-" + str(index)))}
            if trusted:
                entry["certificate_authority"] = str(fixture.cert)
            entries.append(entry)
        self.config.write_text(yaml.safe_dump({
            "credential_groups": {"corporate": {"username": USERNAME}},
            "clusters": entries,
        }))

    def invoke(self, *args, password=PASSWORD):
        # Exercise the actual CLI/getpass, not a patched login method. No secret
        # is present in arguments, environment, or a password file.
        master, slave = pty.openpty()
        attrs = termios.tcgetattr(slave)
        attrs[3] &= ~(termios.ECHO | termios.ECHONL)
        termios.tcsetattr(slave, termios.TCSANOW, attrs)
        process = subprocess.Popen(
            [sys.executable, str(SCRIPT), "--config", str(self.config), *args],
            stdin=slave, stdout=slave, stderr=slave,
            env=self.environment, start_new_session=True,
        )
        os.close(slave)
        output = b""
        prompts = 0
        consumed = 0
        deadline = time.monotonic() + 20
        try:
            while time.monotonic() < deadline:
                readable, _, _ = select.select([master], [], [], 0.1)
                if readable:
                    try:
                        chunk = os.read(master, 65536)
                    except OSError as error:
                        if error.errno == errno.EIO:
                            break
                        raise
                    if not chunk:
                        break
                    output += chunk
                    prompt = re.search(rb"(?i)password[^\r\n]*?: ?", output[consumed:])
                    if prompt:
                        consumed += prompt.end()
                        prompts += 1
                        if prompts > 5:
                            self.fail("unexpected repeated password prompts")
                        os.write(master, (password + "\n").encode())
                elif process.poll() is not None:
                    break
            else:
                self.fail("mcm did not complete within the fixture deadline")
            process.wait(timeout=3)
            text = output.decode(errors="replace")
            self.assertNotIn(password, text, "password echoed or leaked into output")
            self.assertNotIn(TOKEN, text, "session token leaked into output")
            return process.returncode, text, prompts
        finally:
            if process.poll() is None:
                os.killpg(process.pid, signal.SIGKILL)
                process.wait()
            os.close(master)

    def test_group_login_whitespace_password_and_cached_session(self):
        with OAuthFixture() as first, OAuthFixture() as second:
            self.register([first, second])
            code, output, prompts = self.invoke("login")
            self.assertEqual(code, 0, output)
            self.assertEqual(prompts, 1, output)
            self.assertEqual(first.password_matches, [True])
            self.assertEqual(second.password_matches, [True])
            for index in range(2):
                path = self.root / ("kube-" + str(index))
                self.assertEqual(path.stat().st_mode & 0o777, 0o600)
                self.assertNotIn(PASSWORD, path.read_text())
            code, output, prompts = self.invoke("exec", "--command", "oc whoami")
            self.assertEqual(code, 0, output)
            self.assertEqual(prompts, 0, output)
            self.assertIn(USERNAME, output)
            self.assertEqual(first.password_matches, [True])
            self.assertEqual(second.password_matches, [True])

    def test_explicit_insecure_is_invocation_only(self):
        with OAuthFixture() as fixture:
            self.register([fixture], trusted=False)
            code, output, prompts = self.invoke("exec", "--command", "oc whoami",
                                                "--insecure-skip-tls-verify")
            self.assertEqual(code, 0, output)
            self.assertEqual(prompts, 1, output)
            self.assertIn("cluster-0", output)
            self.assertIn("insecure", output.lower())
            persisted = yaml.safe_load((self.root / "kube-0").read_text())
            self.assertTrue(all(not entry["cluster"].get("insecure-skip-tls-verify", False)
                                for entry in persisted["clusters"]))
            code, output, prompts = self.invoke("exec", "--command", "printf SHOULD_NOT_RUN")
            self.assertNotEqual(code, 0, output)
            self.assertEqual(prompts, 0, output)
            self.assertNotIn("SHOULD_NOT_RUN", output)

    def test_tls_failure_never_prompts_or_sends_credentials(self):
        with OAuthFixture() as fixture:
            self.register([fixture], trusted=False)
            code, output, prompts = self.invoke("login")
            self.assertNotEqual(code, 0, output)
            self.assertRegex(output.lower(), r"certificate|tls")
            self.assertEqual(prompts, 0, output)
            self.assertEqual(fixture.password_matches, [])

    def test_rejected_group_password_stops_further_attempts(self):
        with OAuthFixture() as first, OAuthFixture() as second:
            self.register([first, second])
            code, output, prompts = self.invoke("exec", "--command", "printf SHOULD_NOT_RUN",
                                                password="synthetic-wrong-password")
            self.assertNotEqual(code, 0, output)
            self.assertEqual(prompts, 1, output)
            self.assertEqual(first.password_matches, [False])
            self.assertEqual(second.password_matches, [])
            self.assertNotIn("SHOULD_NOT_RUN", output)


if __name__ == "__main__":
    unittest.main()
