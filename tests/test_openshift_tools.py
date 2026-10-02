"""Offline integration tests: real Bash/jq, fixture-backed cluster CLIs."""
import json
import os
from pathlib import Path
import shutil
import sys
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]


class ToolsTest(unittest.TestCase):
    def setUp(self):
        bash = shutil.which("bash")
        jq = shutil.which("jq")
        if bash is None or jq is None:
            self.fail("These integration tests require real bash and jq on PATH")
        self.bash = bash
        self.jq = jq
        self.tmp = tempfile.TemporaryDirectory(dir=os.environ.get("TMPDIR"))
        self.addCleanup(self.tmp.cleanup)
        self.directory = Path(self.tmp.name)
        self.log = self.directory / "argv.jsonl"
        self.fixture = self.directory / "fixture.json"
        self.env = dict(os.environ, PATH=str(self.directory) + os.pathsep + os.environ["PATH"],
                        TOOL_LOG=str(self.log), TOOL_FIXTURE=str(self.fixture))
        fake = '''#!/usr/bin/env python3
import json, os, sys
from pathlib import Path
args = sys.argv[1:]
with open(os.environ['TOOL_LOG'], 'a') as log:
    log.write(json.dumps(args) + '\\n')
data = json.loads(Path(os.environ['TOOL_FIXTURE']).read_text())
key = 'exec' if 'exec' in args else next((x for x in ('pvc', 'pods', 'apirequestcounts') if x in args), 'unknown')
if data.get('fail') == key:
    print('fixture: forbidden or command failed', file=sys.stderr)
    sys.exit(7)
value = data.get(key, {})
print(value if isinstance(value, str) else json.dumps(value))
'''
        for name in ("kubectl", "oc"):
            path = self.directory / name
            path.write_text(fake)
            path.chmod(0o755)
        self.data = {"pvc": {"items": [{"metadata": {"name": "data"}}]},
                     "pods": {"items": [self.pod()]}, "exec": "Filesystem fixture"}

    @staticmethod
    def pod(name="worker", containers=None):
        return {"metadata": {"name": name}, "spec": {
            "volumes": [{"name": "storage", "persistentVolumeClaim": {"claimName": "data"}}],
            "containers": containers if containers is not None else [
                {"name": "app", "volumeMounts": [{"name": "storage", "mountPath": "/data space"}]}]}}

    def run_tool(self, name, *args):
        self.fixture.write_text(json.dumps(self.data))
        self.log.write_text("")
        result = subprocess.run([self.bash, str(ROOT / "openshift" / name), *args],
                                env=self.env, text=True, capture_output=True, timeout=10)
        self.calls = [json.loads(line) for line in self.log.read_text().splitlines()]
        return result

    def test_kdf_scoped_single_pvc_preserves_path(self):
        result = self.run_tool("kdf", "-n", "team", "data")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.calls, [
            ["-n", "team", "get", "pvc", "-o", "json"],
            ["-n", "team", "get", "pods", "-o", "json"],
            ["-n", "team", "exec", "worker", "-c", "app", "--", "df", "-h", "--", "/data space"]])
        self.assertIn("Filesystem fixture", result.stdout)

    def test_kdf_rejects_ambiguous_pods_and_allows_selection(self):
        self.data["pods"]["items"].append(self.pod("other"))
        result = self.run_tool("kdf", "data")
        self.assertEqual(result.returncode, 1)
        self.assertIn("--pod", result.stderr)
        self.assertFalse(any("exec" in call for call in self.calls))
        result = self.run_tool("kdf", "--pod", "worker", "data")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.calls[-1][1], "worker")

    def test_kdf_container_selection_and_multiple_mounts(self):
        mounts = [{"name": "storage", "mountPath": p} for p in ("/z space", "/a", "/a")]
        self.data["pods"]["items"] = [self.pod(containers=[
            {"name": "app", "volumeMounts": mounts},
            {"name": "sidecar", "volumeMounts": mounts}])]
        result = self.run_tool("kdf", "data")
        self.assertEqual(result.returncode, 1)
        self.assertIn("--container", result.stderr)
        for option in ("-c", "--container"):
            result = self.run_tool("kdf", option, "app", "data")
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(self.calls[-1], ["exec", "worker", "-c", "app", "--", "df", "-h", "--", "/a", "/z space"])

    def test_kdf_usage_validation_before_api(self):
        for args in [("--help",), ("-h",)]:
            result = self.run_tool("kdf", *args)
            self.assertEqual(result.returncode, 0)
            self.assertIn("Usage:", result.stdout)
            self.assertEqual(self.calls, [])
        for args in [("--bad",), ("-n",), ("-n", ""), ("-c", "--pod"),
                     ("--pod",), ("a", "b"), ("--namespace", "bad/name"), ("-n", "a", "-n", "b"),
                     ("--", "-evil"), ("",)]:
            with self.subTest(args=args):
                result = self.run_tool("kdf", *args)
                self.assertEqual(result.returncode, 2, result.stderr)
                self.assertEqual(self.calls, [])

    def test_kdf_missing_and_empty_pvcs_are_explicit(self):
        result = self.run_tool("kdf", "missing")
        self.assertEqual(result.returncode, 1)
        self.assertIn("not found", result.stderr)
        self.assertEqual(len(self.calls), 1)
        self.data["pvc"] = {"items": []}
        result = self.run_tool("kdf")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("No PVCs", result.stdout)
        self.assertEqual(len(self.calls), 1)

    def test_ocprems_retains_distinct_callers_with_one_fetch(self):
        users = [
            {"username": "alice", "userAgent": "client-b", "byVerb": [{"verb": "watch"}]},
            {"username": "alice", "userAgent": "client-a", "byVerb": [{"verb": "list"}, {"verb": "get"}]}]
        self.data["apirequestcounts"] = {"items": [
            {"metadata": {"name": "ignored"}, "status": {"removedInRelease": ""}},
            {"metadata": {"name": "widgets.v1.example"}, "status": {
                "removedInRelease": "4.20", "requestCount": 42,
                "last24h": [{"byNode": [{"byUser": users}]}]}}]}
        result = self.run_tool("ocprems")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.calls, [["get", "apirequestcounts", "-o", "json"]])
        self.assertEqual(result.stdout, 'API: widgets.v1.example\nRemoved in release: 4.20\nRequest count: 42\nLast 24h callers (distinct user/verb/user-agent):\nUSER\tVERB\tUSER AGENT\nalice\tget\tclient-a\nalice\tlist\tclient-a\nalice\twatch\tclient-b\n\n')

    def test_ocprems_null_empty_and_incomplete_callers(self):
        for items in ([], None, [{"metadata": {"name": "unknown"}, "status": None}]):
            self.data["apirequestcounts"] = {"items": items}
            result = self.run_tool("ocprems")
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(result.stdout, "No APIs with a reported removal release.\n")
        status = {"removedInRelease": "4.20", "requestCount": None, "last24h": None}
        self.data["apirequestcounts"] = {"items": [{"metadata": {"name": "api"}, "status": status}]}
        result = self.run_tool("ocprems")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("Request count: unknown", result.stdout)
        self.assertIn("(no caller data)", result.stdout)
        status["last24h"] = [{"byNode": None}, {"byNode": [{"byUser": [
            {"username": "alice", "byVerb": None},
            {"username": "bob", "userAgent": "agent\twith\ncontrols", "byVerb": [{"verb": "get"}]}]}]}]
        result = self.run_tool("ocprems")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("alice\tunknown\tunknown\n", result.stdout)
        self.assertIn("bob\tget\tagent\\twith\\ncontrols\n", result.stdout)

    def test_ocprems_usage_validation_before_api(self):
        for option in ("-h", "--help"):
            result = self.run_tool("ocprems", option)
            self.assertEqual(result.returncode, 0)
            self.assertIn("Usage:", result.stdout)
            self.assertEqual(self.calls, [])
        for args in [("--bad",), ("-n", "team"), ("api",), ("--help", "extra")]:
            result = self.run_tool("ocprems", *args)
            self.assertEqual(result.returncode, 2, result.stderr)
            self.assertEqual(self.calls, [])

    def test_dependencies_checked_before_cluster_calls(self):
        (self.directory / "python3").symlink_to(sys.executable)
        self.env["PATH"] = str(self.directory)
        for tool, cli in (("kdf", "kubectl"), ("ocprems", "oc")):
            result = self.run_tool(tool)
            self.assertEqual(result.returncode, 1, result.stderr)
            self.assertIn("required command not found: jq", result.stderr)
            self.assertEqual(self.calls, [])
            result = self.run_tool(tool, "--help")
            self.assertEqual(result.returncode, 0)
        (self.directory / "jq").symlink_to(self.jq)
        for tool, cli in (("kdf", "kubectl"), ("ocprems", "oc")):
            (self.directory / cli).unlink()
            result = self.run_tool(tool)
            self.assertEqual(result.returncode, 1, result.stderr)
            self.assertIn("required command not found: " + cli, result.stderr)
            self.assertEqual(self.calls, [])

    def test_invalid_api_output_is_not_success(self):
        for tool, resource in (("kdf", "pvc"), ("kdf", "pods"), ("ocprems", "apirequestcounts")):
            original = self.data.get(resource)
            for value in ("", "not json", None, [], {"items": "bad"}):
                with self.subTest(tool=tool, resource=resource, value=value):
                    self.data[resource] = value
                    result = self.run_tool(tool)
                    self.assertEqual(result.returncode, 1, result.stdout)
                    self.assertTrue(result.stderr)
                    self.assertFalse(any("exec" in call for call in self.calls))
            self.data[resource] = original

    def test_kdf_null_lists_and_optional_pod_fields(self):
        self.data["pvc"] = {"items": None}
        result = self.run_tool("kdf")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("No PVCs", result.stdout)
        self.data["pvc"] = {"items": [{"metadata": {"name": "data"}}]}
        for pods in ({"items": None}, {"items": [self.pod(containers=[])]},
                     {"items": [{"metadata": {"name": "no-volumes"}, "spec": {"containers": None}}]}):
            self.data["pods"] = pods
            result = self.run_tool("kdf", "data")
            self.assertEqual(result.returncode, 1)
            self.assertIn("found 0", result.stderr)
            self.assertFalse(any("exec" in call for call in self.calls))

    def test_kdf_rejects_missing_mount_paths(self):
        for path in (None, "", 3, "relative", "/bad\x00path"):
            with self.subTest(path=path):
                self.data["pods"]["items"] = [self.pod(containers=[{
                    "name": "app", "volumeMounts": [{"name": "storage", "mountPath": path}]}])]
                result = self.run_tool("kdf", "data")
                self.assertEqual(result.returncode, 1)
                self.assertIn("invalid mount", result.stderr)
                self.assertFalse(any("exec" in call for call in self.calls))

    def test_cluster_and_exec_errors_normalized_to_one(self):
        for tool, resource, expected_calls in (("kdf", "pvc", 1), ("kdf", "pods", 2),
                                                ("kdf", "exec", 3), ("ocprems", "apirequestcounts", 1)):
            with self.subTest(tool=tool, resource=resource):
                self.data["fail"] = resource
                result = self.run_tool(tool)
                self.assertEqual(result.returncode, 1)
                self.assertIn("fixture: forbidden or command failed", result.stderr)
                self.assertEqual(len(self.calls), expected_calls)

    def test_kdf_all_pvcs_sorted_shared_snapshots_and_volume_aliases(self):
        other = self.pod("other")
        other["spec"]["volumes"][0]["persistentVolumeClaim"]["claimName"] = "aaa"
        pod = self.data["pods"]["items"][0]
        pod["spec"]["volumes"].append({"name": "alias", "persistentVolumeClaim": {"claimName": "data"}})
        unusual_path = "/line\nwith 'quotes' \\ backslash"
        pod["spec"]["containers"][0]["volumeMounts"].append({"name": "alias", "mountPath": unusual_path})
        self.data["pvc"]["items"].append({"metadata": {"name": "aaa"}})
        self.data["pods"]["items"].append(other)
        result = self.run_tool("kdf", "--namespace", "team")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertLess(result.stdout.index("aaa:"), result.stdout.index("data:"))
        self.assertEqual(len(self.calls), 4)
        self.assertTrue(all(call[:2] == ["-n", "team"] for call in self.calls))
        self.assertEqual(self.calls[-1][-2:], ["/data space", unusual_path])
        result = self.run_tool("kdf", "--", "data")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertNotIn("aaa:", result.stdout)

    def test_kdf_unmatched_selectors_never_exec(self):
        for args in [("--pod", "missing"), ("--container", "missing")]:
            result = self.run_tool("kdf", *args, "data")
            self.assertEqual(result.returncode, 1)
            self.assertIn("found 0", result.stderr)
            self.assertFalse(any("exec" in call for call in self.calls))

    def test_ocprems_deterministic_sort_and_exact_tuple_dedup(self):
        user = {"username": "alice", "userAgent": "client", "byVerb": [{"verb": "get"}]}
        self.data["apirequestcounts"] = {"items": [
            {"metadata": {"name": name}, "status": {"removedInRelease": "4.20", "requestCount": 0,
                "last24h": [{"byNode": [{"byUser": [user, user]}]},
                            {"byNode": [{"byUser": [user]}]}]}}
            for name in ("zzz", "aaa")]}
        result = self.run_tool("ocprems")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertLess(result.stdout.index("API: aaa"), result.stdout.index("API: zzz"))
        self.assertEqual(result.stdout.count("alice\tget\tclient\n"), 2)
        self.assertEqual(result.stdout.count("Request count: 0"), 2)
        self.assertEqual(self.calls, [["get", "apirequestcounts", "-o", "json"]])


if __name__ == "__main__":
    unittest.main()
