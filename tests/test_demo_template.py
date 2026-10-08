"""Exercise the copyable demo runner with real Bash commands."""
import os
import pty
import select
import termios
import time
from pathlib import Path
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "templates/demo-template.sh"


class DemoTemplateTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.work = Path(self.tmp.name)

    def run_demo(self, body, answers=""):
        return subprocess.run(
            ["bash", "--noprofile", "--norc", "-c", 'source "$1"; ' + body,
             "demo-test", str(SCRIPT)], input=answers, text=True,
            capture_output=True, cwd=self.work, timeout=5,
            env=dict(os.environ, NO_COLOR="1"))

    def tty_output(self, body, term='xterm', no_color=None):
        master, slave = pty.openpty()
        self.addCleanup(os.close, master)
        env = dict(os.environ, TERM=term)
        env.pop('NO_COLOR', None)
        if no_color is not None:
            env['NO_COLOR'] = no_color
        process = subprocess.Popen(
            ['bash', '-c', 'source "$1"; ' + body, 'demo-test', str(SCRIPT)],
            stdin=subprocess.PIPE, stdout=slave, stderr=slave, env=env)
        os.close(slave)
        assert process.stdin is not None
        self.addCleanup(process.stdin.close)
        self.addCleanup(lambda: process.poll() is None and process.kill())
        # Keep stdin open: clean/section must return without waiting for input.
        process.wait(timeout=5)
        self.assertEqual(process.returncode, 0)
        data = b''
        while select.select([master], [], [], 1)[0]:
            try:
                chunk = os.read(master, 65536)
            except OSError:
                break
            if not chunk:
                break
            data += chunk
        return data.decode()

    def test_clean_is_immediate_and_preserves_scrollback(self):
        output = self.tty_output('printf before; clean; printf after')
        self.assertEqual(output, 'before\x1b[2J\x1b[Hafter')
        for term in ('', 'dumb'):
            self.assertEqual(self.tty_output('clean; printf after', term=term), 'after')
        result = self.run_demo('clean; printf after')
        self.assertEqual(result.stdout, 'after')
        self.assertEqual(result.stderr, '')
        self.assertEqual(result.returncode, 0)

    def test_section_and_explanation_have_tty_only_styles(self):
        body = "section 'Deployment'; explain 'Inspect the pods.'"
        output = self.tty_output(body)
        self.assertIn('\x1b[1m', output)
        self.assertIn('\x1b[2m', output)
        self.assertIn('Deployment', output)
        for options in ({'no_color': ''}, {'term': 'dumb'}, {'term': ''}):
            with self.subTest(options=options):
                self.assertNotIn('\x1b', self.tty_output(body, **options))
        result = self.run_demo(body)
        self.assertEqual(result.stdout, '\n== Deployment ==\n\nInspect the pods.\n')

    def test_controls_once_and_multiline_display(self):
        result = subprocess.run(['bash', str(SCRIPT)], input='q\n', text=True,
                                capture_output=True, timeout=5)
        self.assertEqual(result.stdout.count('Enter: continue'), 1)
        result = self.run_demo("run 'printf first\\n'", '\n')
        self.assertNotIn('execute /', result.stdout)
        result = self.run_demo("run 'printf first\nprintf second'", '\n')
        self.assertIn('$ printf first\n> printf second\nfirstsecond', result.stdout)

    def test_wait_suppresses_terminal_echo_and_restores_it(self):
        master, slave = pty.openpty()
        self.addCleanup(os.close, master)
        self.addCleanup(os.close, slave)
        process = subprocess.Popen(
            ['bash', '-c', 'source "$1"; wait; printf continued', 'demo-test', str(SCRIPT)],
            stdin=slave, stdout=slave, stderr=slave)
        self.addCleanup(lambda: process.poll() is None and process.kill())
        deadline = time.monotonic() + 5
        while termios.tcgetattr(slave)[3] & termios.ECHO:
            self.assertLess(time.monotonic(), deadline, 'wait did not disable echo')
            time.sleep(0.01)
        os.write(master, b'q\n')
        process.wait(timeout=5)
        self.assertEqual(process.returncode, 0)
        self.assertTrue(termios.tcgetattr(slave)[3] & termios.ECHO)
        self.assertTrue(select.select([master], [], [], 1)[0])
        self.assertEqual(os.read(master, 65536), b'\r\n')

    def test_help_and_invalid_arguments_do_not_start_demo(self):
        for args, status in ((['--help'], 0), (['-h'], 0), (['--bad'], 2),
                             (['--help', 'extra'], 2)):
            with self.subTest(args=args):
                result = subprocess.run(['bash', str(SCRIPT), *args],
                                        input='', text=True, capture_output=True, timeout=5)
                self.assertEqual(result.returncode, status)
                self.assertIn('Usage:', result.stdout + result.stderr)
                self.assertNotIn('[Enter]', result.stdout)

    def test_enter_executes_but_quit_does_not(self):
        result = self.run_demo("run 'printf first > first'; run 'printf second > second'", "\nq\n")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertTrue((self.work / "first").exists())
        self.assertFalse((self.work / "second").exists())
        self.assertIn("$ printf first > first", result.stdout)
        self.assertNotIn("execute /", result.stdout)
        self.assertNotIn("Enter", result.stdout)

    def test_wait_is_explicit_and_accepts_enter_quit_or_eof(self):
        for answers, expected in (('\n', True), ('q\n', False), ('', False)):
            with self.subTest(answers=answers):
                result = self.run_demo("wait; printf done > marker", answers)
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertEqual((self.work / 'marker').exists(), expected)
                if expected:
                    (self.work / 'marker').unlink()
                self.assertEqual(result.stdout, '\n')

    def test_wait_does_not_consume_interactive_command_input(self):
        result = self.run_demo(
            "wait; run 'read -r reply; printf \"%s\" \"$reply\" > result'",
            '\n\naudience input\n')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual((self.work / 'result').read_text(), 'audience input')

    def test_failures_and_pipeline_failures_continue(self):
        result = self.run_demo("run 'false'; run 'false | true'; run 'printf done'; run 'false'",
                               "\n" * 4)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout.count('[exit 1]'), 3)
        self.assertIn('done', result.stdout)

    def test_state_multiline_redirection_and_interactive_input(self):
        (self.work / 'subdir').mkdir()
        body = """run 'cd subdir'
run 'export DEMO_VALUE=hello'
run 'printf "%s\\n" "$DEMO_VALUE" | tr a-z A-Z > result'
run 'cat <<EOF >> result
second line
EOF'
run 'read -r reply; printf "%s\\n" "$reply" >> result'
"""
        result = self.run_demo(body, "\n" * 5 + "audience input\n")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual((self.work / 'subdir/result').read_text(),
                         'HELLO\nsecond line\naudience input\n')

    def test_eof_and_untrusted_prompt_input_never_execute(self):
        for answers in ('', 'printf pwned > injected\n', 'q\n'):
            with self.subTest(answers=answers):
                result = self.run_demo("run 'printf bad > executed'", answers)
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertFalse((self.work / 'executed').exists())
                self.assertFalse((self.work / 'injected').exists())
                self.assertNotIn('\x1b', result.stdout)


if __name__ == "__main__":
    unittest.main()
