"""Fixture-only tests: no cluster access or host process assumptions."""
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]


class LinuxToolsTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.work = Path(self.tmp.name)
        self.bin = self.work / "bin"
        self.bin.mkdir()
        self.env = dict(os.environ, PATH=f"{self.bin}:{os.environ['PATH']}")

    def stub(self, name, body):
        path = self.bin / name
        path.write_text("#!/bin/bash\n" + body)
        path.chmod(0o755)

    def run_tool(self, tool, *args, cwd=None, env=None):
        return subprocess.run(["/bin/bash", str(ROOT / "linux" / tool), *args],
                              cwd=cwd or self.work, env=env or self.env,
                              text=True, capture_output=True, timeout=10)

    def test_help_and_usage_before_dependencies(self):
        env = dict(self.env, PATH=str(self.bin))
        for tool in ("lsswap", "pls"):
            for flag in ("-h", "--help"):
                with self.subTest(tool=tool, flag=flag):
                    result = self.run_tool(tool, flag, env=env)
                    self.assertEqual(result.returncode, 0, result.stderr)
                    self.assertIn("Usage:", result.stdout)
            for args in (("unexpected",), ("--help", "extra")):
                with self.subTest(tool=tool, args=args):
                    self.assertEqual(self.run_tool(tool, *args, env=env).returncode, 2)

    def proc_fixture(self):
        proc = self.work / "proc fixture"
        proc.mkdir()
        self.env["PROC_ROOT"] = str(proc)
        for pid in range(1, 13):
            directory = proc / str(pid)
            directory.mkdir()
            (directory / "status").write_text(
                f"Name:\tworker with spaces {pid}\nPid:\t{pid}\nVmSwap:\t{pid * 1024} kB\n")
        (proc / "90").mkdir()  # vanished status
        (proc / "91").mkdir()
        (proc / "91" / "status").write_text("Name:\tkernel thread\nPid:\t91\n")
        if os.geteuid() != 0:
            (proc / "92").mkdir()
            (proc / "92" / "status").write_text("Name:\tprivate\nPid:\t92\nVmSwap:\t999999 kB\n")
            (proc / "92" / "status").chmod(0)
        # Root bypasses permissions; the unreadable-process case is non-root only.
        return proc

    def test_lsswap_proc_snapshot_top_ten(self):
        self.proc_fixture()
        result = self.run_tool("lsswap")
        self.assertEqual(result.returncode, 0, result.stderr)
        rows = result.stdout.splitlines()
        self.assertIn("SWAP(MiB)", rows[0])
        self.assertEqual(len(rows), 11)
        self.assertEqual([float(row.split()[0]) for row in rows[1:]], list(range(12, 2, -1)))
        self.assertIn("worker with spaces 12", rows[1])
        self.assertEqual(result.stderr, "")

    def test_lsswap_limit_validation(self):
        self.proc_fixture()
        for limit in ("1", "02", "100", "9999999999999999999999999999999999"):
            with self.subTest(limit=limit):
                result = self.run_tool("lsswap", "--limit", limit)
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertEqual(len(result.stdout.splitlines()), min(int(limit), 12) + 1)
        for args in (("--limit",), ("--limit", "0"), ("--limit", "00"),
                     ("--limit", "-1"), ("--limit", "1.5"), ("--limit", "wat"),
                     ("--limit", "1", "extra")):
            with self.subTest(args=args):
                self.assertEqual(self.run_tool("lsswap", *args).returncode, 2)

    def test_pls_conmon_names_and_false_positives(self):
        self.env["PS_FIXTURE"] = str(self.work / "ps.txt")
        Path(self.env["PS_FIXTURE"]).write_text(
            "zoe conmon /usr/bin/conmon -n short -c id\n"
            "amy conmon /usr/libexec/podman/conmon --name long name --cid id\n"
            "bob conmon conmon --name=equal name -c id\n"
            "eve bash bash -c echo conmon -n unrelated\n"
            "eve notconmon /usr/bin/notconmon -n fake\n"
            "eve conmon-helper /usr/bin/conmon-helper -n fake\n"
            "eve conmon /usr/bin/echo conmon -n fake\n"
            "eve conmon /usr/bin/conmon --no-name foo\n")
        self.stub("ps", '/bin/cat "$PS_FIXTURE"\n')
        result = self.run_tool("pls")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout.splitlines(), ["amy:long name", "bob:equal name", "zoe:short"])

    def test_pls_empty_success_and_ps_failure(self):
        for text in ("", "amy bash bash conmon -n fake", "amy conmon conmon --name=", "amy conmon conmon -n --cid x"):
            with self.subTest(text=text):
                self.env["PS_TEXT"] = text
                self.stub("ps", 'printf "%s\\n" "$PS_TEXT"\n')
                result = self.run_tool("pls")
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertEqual(result.stdout, "")
        self.stub("ps", 'printf "ps failed\\n" >&2\nexit 42\n')
        result = self.run_tool("pls")
        self.assertEqual(result.returncode, 1)
        self.assertIn("ps failed", result.stderr)

    def test_missing_dependencies_are_operational_errors(self):
        proc = self.proc_fixture()
        for tool, deps in (("lsswap", ("sort", "awk")),
                           ("pls", ("ps", "sort"))):
            for missing in deps:
                with self.subTest(tool=tool, missing=missing):
                    isolated = self.work / f"{tool}-{missing}"
                    isolated.mkdir()
                    for dep in deps:
                        if dep != missing:
                            target = shutil.which(dep)
                            self.assertIsNotNone(target)
                            assert target is not None
                            (isolated / dep).symlink_to(target)
                    result = self.run_tool(tool,
                                           env=dict(self.env, PATH=str(isolated), PROC_ROOT=str(proc)))
                    self.assertEqual(result.returncode, 1, result.stderr)
                    self.assertTrue(result.stderr)

    def test_lsswap_invalid_proc_root(self):
        self.env["PROC_ROOT"] = str(self.work / "missing")
        result = self.run_tool("lsswap")
        self.assertEqual(result.returncode, 1)
        self.assertTrue(result.stderr)

    def test_pipeline_dependency_failures(self):
        self.proc_fixture()
        self.stub("ps", 'printf "amy conmon conmon -n okay\\n"\n')
        self.stub("sort", 'printf "sort failed\\n" >&2\nexit 27\n')
        for tool in ("lsswap", "pls"):
            with self.subTest(tool=tool):
                result = self.run_tool(tool)
                self.assertEqual(result.returncode, 1)
                self.assertIn("sort failed", result.stderr)

    def test_lsswap_preserves_name_and_fractional_swap(self):
        proc = self.work / "proc"
        (proc / "1").mkdir(parents=True)
        self.env["PROC_ROOT"] = str(proc)
        (proc / "1" / "status").write_text("Name:\t  spaced worker  \nPid:\t1\nVmSwap:\t512 kB\n")
        result = self.run_tool("lsswap")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout.splitlines()[1], f"{0.5:<14.2f} {'1':<14} {'  spaced worker  '}")
        self.assertEqual(result.stdout.splitlines()[1].split()[0], "0.50")
        (proc / "1" / "status").unlink()
        result = self.run_tool("lsswap")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(len(result.stdout.splitlines()), 1)


if __name__ == "__main__":
    unittest.main()
