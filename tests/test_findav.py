"""Offline findav regressions, with deterministic I/O failures even as root."""
import os
from pathlib import Path
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]


class FindavTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.work = Path(self.tmp.name)
        self.bin = self.work / "bin"
        self.bin.mkdir()
        self.scan = self.work / "scan space"
        self.scan.mkdir()
        self.env = dict(os.environ, PATH=f"{self.bin}:{os.environ['PATH']}")

    def stub(self, name, body):
        path = self.bin / name
        path.write_text("#!/bin/bash\n" + body)
        path.chmod(0o755)

    def run_tool(self, *args, env=None):
        return subprocess.run(
            ["/bin/bash", str(ROOT / "linux/findav"), *args],
            cwd=self.work, env=env or self.env, capture_output=True, timeout=10)

    def test_traversal_failure_reports_incomplete_even_after_match(self):
        vault = self.scan / "vault"
        vault.write_bytes(b"$ANSIBLE_VAULT;1.1;AES256\nbody\n")
        self.env["FOUND_FILE"] = str(vault)
        self.stub("find", 'printf "%s\\0" "$FOUND_FILE"\nprintf "traversal failed\\n" >&2\nexit 7\n')
        result = self.run_tool(str(self.scan))
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(result.stdout, os.fsencode(vault) + b"\n")
        self.assertIn(b"incomplete", result.stderr.lower())
        self.assertIn(b"traversal failed", result.stderr)

    def test_file_read_failure_is_not_a_match_or_success(self):
        vault = self.scan / "bad read"
        vault.write_bytes(b"$ANSIBLE_VAULT;1.1;AES256\n")
        # A failing reader can emit a partial header before its I/O error.
        self.stub("head", "printf '$ANSIBLE_VAULT;1.1;AES256\\n'\nexit 1\n")
        for start in (vault, self.scan):
            with self.subTest(start=start):
                result = self.run_tool(str(start))
                self.assertNotEqual(result.returncode, 0)
                self.assertEqual(result.stdout, b"")
                self.assertIn(b"incomplete", result.stderr.lower())
                self.assertIn(os.fsencode(vault), result.stderr)

    def test_literal_dash_vault_file_with_empty_stdin(self):
        (self.work / "-").write_bytes(b"$ANSIBLE_VAULT;1.1;AES256\n")
        result = subprocess.run(
            ["/bin/bash", str(ROOT / "linux/findav"), "--", "-"],
            cwd=self.work, env=self.env, input=b"", capture_output=True, timeout=10)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout, b"-\n")
        self.assertEqual(result.stderr, b"")

    def test_literal_dash_plain_file_ignores_vault_stdin(self):
        (self.work / "-").write_bytes(b"plain\n")
        result = subprocess.run(
            ["/bin/bash", str(ROOT / "linux/findav"), "--", "-"],
            cwd=self.work, env=self.env, input=b"$ANSIBLE_VAULT;1.1;AES256\n",
            capture_output=True, timeout=10)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout, b"")
        self.assertEqual(result.stderr, b"")

    def test_literal_dash_file_does_not_wait_for_stdin(self):
        (self.work / "-").write_bytes(b"$ANSIBLE_VAULT;1.1;AES256\n")
        with subprocess.Popen(
            ["/bin/bash", str(ROOT / "linux/findav"), "-0", "--", "-"],
            cwd=self.work, env=self.env, stdin=subprocess.PIPE,
            stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        ) as process:
            try:
                # Keep stdin open without supplying bytes or EOF.
                process.wait(timeout=3)
            except subprocess.TimeoutExpired:
                self.fail("literal '-' file scan blocked waiting for stdin")
            finally:
                # Closing stdin also releases the buggy reader after a failure.
                stdout, stderr = process.communicate(timeout=10)
        self.assertEqual(process.returncode, 0, stderr)
        self.assertEqual(stdout, b"-\0")
        self.assertEqual(stderr, b"")

    def test_leading_dash_directory_after_option_terminator(self):
        directory = self.work / "-scan"
        directory.mkdir()
        (directory / "vault space").write_bytes(b"$ANSIBLE_VAULT;1.1;AES256")
        result = self.run_tool("--", "-scan")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout, b"./-scan/vault space\n")

    def test_null_output_preserves_filename_bytes(self):
        names = ("has space", "has\nnewline", "-leading", "back\\slash")
        for name in names:
            (self.scan / name).write_bytes(b"$ANSIBLE_VAULT;1.2;AES256;label")
        for option in ("-0", "--null"):
            with self.subTest(option=option):
                result = self.run_tool(option, str(self.scan))
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertEqual(set(result.stdout.split(b"\0")[:-1]),
                                 {os.fsencode(self.scan / name) for name in names})
                self.assertTrue(result.stdout.endswith(b"\0"))
                direct = self.run_tool(option, str(self.scan / names[1]))
                self.assertEqual(direct.returncode, 0, direct.stderr)
                self.assertEqual(direct.stdout, os.fsencode(self.scan / names[1]) + b"\0")

    def test_expression_like_starting_directories(self):
        for name in ("!", "("):
            with self.subTest(name=name):
                directory = self.work / name
                directory.mkdir()
                (directory / "vault").write_bytes(b"$ANSIBLE_VAULT;")
                result = self.run_tool("--", name)
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertEqual(result.stdout, os.fsencode(f"./{name}/vault\n"))

    def test_vanished_file_marks_scan_incomplete_and_continues(self):
        vault = self.scan / "good"
        vault.write_bytes(b"$ANSIBLE_VAULT;")
        missing = self.scan / "vanished"
        self.env.update(MISSING_FILE=str(missing), FOUND_FILE=str(vault))
        self.stub("find", 'printf "%s\\0" "$MISSING_FILE" "$FOUND_FILE"\n')
        result = self.run_tool(str(self.scan))
        self.assertEqual(result.returncode, 1)
        self.assertEqual(result.stdout, os.fsencode(vault) + b"\n")
        self.assertIn(os.fsencode(missing), result.stderr)
        self.assertIn(b"incomplete", result.stderr)

    @unittest.skipIf(os.geteuid() == 0, "root bypasses mode permissions; stubs cover I/O errors")
    def test_unreadable_file(self):
        vault = self.scan / "private"
        vault.write_bytes(b"$ANSIBLE_VAULT;")
        vault.chmod(0)
        self.addCleanup(vault.chmod, 0o600)
        for start in (vault, self.scan):
            with self.subTest(start=start):
                result = self.run_tool(str(start))
                self.assertEqual(result.returncode, 1)
                self.assertEqual(result.stdout, b"")
                self.assertIn(b"incomplete", result.stderr)
                self.assertIn(os.fsencode(vault), result.stderr)

    def test_scope_prefix_and_eof_compatibility(self):
        contents = {
            "header no newline": b"$ANSIBLE_VAULT;1.1;AES256",
            "header body": b"$ANSIBLE_VAULT;1.2;AES256;label\n012345\n",
            "empty": b"",
            "plain": b"not vaulted\n",
            "yaml": b"secret: !vault |\n  $ANSIBLE_VAULT;1.1;AES256\n",
            "later": b"\n$ANSIBLE_VAULT;1.1;AES256\n",
            "indented": b" $ANSIBLE_VAULT;1.1;AES256\n",
        }
        for name, content in contents.items():
            (self.scan / name).write_bytes(content)
        nested = self.scan / "nested"
        nested.mkdir()
        (nested / "vault").write_bytes(b"$ANSIBLE_VAULT;")
        expected = {os.fsencode(self.scan / name) for name in contents if name.startswith("header")}
        for args in ((), ("--recursive",), ("-r",)):
            with self.subTest(args=args):
                result = self.run_tool(*args, str(self.scan))
                self.assertEqual(result.returncode, 0, result.stderr)
                wanted = expected | ({os.fsencode(nested / "vault")} if args else set())
                self.assertEqual(set(result.stdout.splitlines()), wanted)
                self.assertEqual(result.stderr, b"")

    def test_no_matches_and_direct_file_compatibility(self):
        for name, content, expected in (
            ("empty", b"", b""),
            ("plain", b"plain\n", b""),
            ("-vault", b"$ANSIBLE_VAULT;", b"-vault\n"),
        ):
            (self.work / name).write_bytes(content)
            result = self.run_tool("--", name)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(result.stdout, expected)
        result = self.run_tool(str(self.scan))
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout, b"")

    def test_symlink_scope_is_unchanged(self):
        target = self.work / "outside"
        target.mkdir()
        vault = target / "vault"
        vault.write_bytes(b"$ANSIBLE_VAULT;")
        link = self.scan / "file link"
        link.symlink_to(vault)
        directory_link = self.scan / "dir link"
        directory_link.symlink_to(target, target_is_directory=True)
        for start in (self.scan, directory_link):
            result = self.run_tool("-r", str(start))
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(result.stdout, b"")
        direct = self.run_tool(str(link))
        self.assertEqual(direct.returncode, 0, direct.stderr)
        self.assertEqual(direct.stdout, os.fsencode(link) + b"\n")

    def test_help_and_unknown_options_without_external_commands(self):
        env = dict(self.env, PATH=str(self.bin))
        for option in ("-h", "--help"):
            result = self.run_tool(option, env=env)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn(b"Usage:", result.stdout)
            self.assertIn(b"--null", result.stdout)
        for args in (("--unknown",), ("-x",), ("one", "two")):
            result = self.run_tool(*args, env=env)
            self.assertEqual(result.returncode, 2)
            self.assertEqual(result.stdout, b"")
            self.assertIn(b"Usage:", result.stderr)

    def test_invalid_start_paths(self):
        fifo = self.work / "fifo"
        os.mkfifo(fifo)
        for start in (self.work / "missing", fifo):
            result = self.run_tool(str(start))
            self.assertEqual(result.returncode, 1)
            self.assertEqual(result.stdout, b"")
            self.assertIn(b"Error:", result.stderr)


if __name__ == "__main__":
    unittest.main()
