"""Offline gencl regressions using real, isolated Git repositories."""
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]


class GenclTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.work = Path(self.tmp.name)
        self.env = {key: value for key, value in os.environ.items()
                    if not key.startswith("GIT_")}
        self.env.update(GIT_CONFIG_NOSYSTEM="1", GIT_CONFIG_GLOBAL=os.devnull,
                        GIT_AUTHOR_NAME="Test", GIT_AUTHOR_EMAIL="test@example.invalid",
                        GIT_COMMITTER_NAME="Test", GIT_COMMITTER_EMAIL="test@example.invalid")
        self.git("init", "-q")
        self.git("commit", "-q", "--allow-empty", "-m", "first commit")

    def git(self, *args):
        return subprocess.run(["git", *args], cwd=self.work, env=self.env,
                              text=True, capture_output=True, check=True, timeout=10).stdout.strip()

    def run_tool(self, *args, env=None, cwd=None):
        return subprocess.run([sys.executable, str(ROOT / "linux/gencl"), *args],
                              cwd=cwd or self.work, env=env or self.env,
                              text=True, capture_output=True, timeout=10)

    def test_shell_metacharacters_in_valid_tag_are_literal(self):
        tag = "v1;touch${IFS}pwned;#"
        self.git("check-ref-format", "refs/tags/" + tag)
        self.git("tag", tag)
        self.git("commit", "-q", "--allow-empty", "-m", "second commit")
        result = self.run_tool()
        self.assertFalse((self.work / "pwned").exists(), "tag executed shell code")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stderr, "")
        self.assertIn("## HEAD\n" + self.git("log", "-1", "--oneline"), result.stdout)
        self.assertIn("## " + tag + "\n", result.stdout)

    def test_option_like_tag_and_branch_collision_use_exact_tag_refs(self):
        first = self.git("rev-parse", "HEAD")
        self.git("update-ref", "refs/tags/-n1", first)
        self.git("tag", "v1")
        self.git("commit", "-q", "--allow-empty", "-m", "second commit")
        self.git("branch", "v1")
        result = self.run_tool()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stderr, "")
        self.assertEqual(result.stdout.count("first commit"), 1)
        self.assertEqual(result.stdout.count("second commit"), 1)

    def test_git_failure_returns_nonzero_without_partial_report(self):
        self.git("tag", "v2")
        (self.work / ".git/refs/tags/v1").write_text("1" * 40 + "\n")
        result = self.run_tool()
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(result.stdout, "")
        self.assertIn("gencl:", result.stderr)
        self.assertIn("fatal:", result.stderr)

    def test_help_without_git(self):
        env = dict(self.env, PATH=str(self.work / "absent"))
        for flag in ("-h", "--help"):
            with self.subTest(flag=flag):
                result = self.run_tool(flag, env=env)
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertIn("Usage:", result.stdout)
                self.assertIn("heading", result.stdout)
                self.assertEqual(result.stderr, "")

    def test_invalid_arguments_are_rejected_before_git(self):
        env = dict(self.env, PATH=str(self.work / "absent"))
        for args in (("one", "two"), ("--help", "extra"), ("--unknown",)):
            with self.subTest(args=args):
                result = self.run_tool(*args, env=env)
                self.assertEqual(result.returncode, 2)
                self.assertEqual(result.stdout, "")
                self.assertIn("Usage:", result.stderr)
                self.assertNotIn("Traceback", result.stderr)

    def test_missing_git_is_a_concise_error(self):
        result = self.run_tool(env=dict(self.env, PATH=str(self.work / "absent")))
        self.assertEqual(result.returncode, 1)
        self.assertEqual(result.stdout, "")
        self.assertIn("gencl:", result.stderr)
        self.assertNotIn("Traceback", result.stderr)

    def test_no_tags_preserves_full_history_and_literal_heading(self):
        self.git("commit", "-q", "--allow-empty", "-m", "second commit")
        heading = "Release $(touch pwned)"
        result = self.run_tool(heading)
        expected = self.git("log", "--oneline", "--no-merges", "--no-decorate")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout, f"# Changelog\n\n## {heading}\n{expected}\n")
        self.assertFalse((self.work / "pwned").exists())

    def test_version_order_ranges_annotated_tags_and_merge_exclusion(self):
        first = self.git("log", "-1", "--oneline")
        self.git("tag", "v1")
        self.git("commit", "-q", "--allow-empty", "-m", "second commit")
        second = self.git("log", "-1", "--oneline")
        self.git("tag", "-a", "v2", "-m", "release two")
        self.git("commit", "-q", "--allow-empty", "-m", "third commit")
        third = self.git("log", "-1", "--oneline")
        self.git("tag", "v10")
        self.git("checkout", "-q", "-b", "side")
        self.git("commit", "-q", "--allow-empty", "-m", "side commit")
        side = self.git("log", "-1", "--oneline")
        self.git("checkout", "-q", "-b", "mainline", "v10")
        self.git("merge", "--no-ff", "side", "-m", "excluded merge")
        # A pathname matching a tag must not change revision interpretation.
        (self.work / "v10").write_text("untracked")
        result = self.run_tool("Upcoming")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout,
                         f"# Changelog\n\n## Upcoming\n{side}\n\n"
                         f"## v10\n{third}\n\n## v2\n{second}\n\n## v1\n{first}\n\n")

    def test_empty_repository_and_non_repository_fail_without_stdout(self):
        outside = self.work / "outside"
        outside.mkdir()
        # Stop Git's repository discovery before it can reach the parent fixture.
        env = dict(self.env, GIT_CEILING_DIRECTORIES=str(self.work))
        result = self.run_tool(cwd=outside, env=env)
        self.assertEqual(result.returncode, 1)
        self.assertEqual(result.stdout, "")
        self.assertIn("gencl:", result.stderr)
        self.git("init", "-q", str(outside))
        result = self.run_tool(cwd=outside)
        self.assertEqual(result.returncode, 1)
        self.assertEqual(result.stdout, "")
        self.assertIn("gencl:", result.stderr)


if __name__ == "__main__":
    unittest.main()
