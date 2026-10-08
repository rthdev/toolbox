"""Exercise the copyable demo runner with real Bash commands."""
import os
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
        self.assertIn("Enter", result.stdout)

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
