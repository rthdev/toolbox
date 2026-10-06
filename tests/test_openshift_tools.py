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
if data.get('fail') == key or (key == 'exec' and args[args.index('exec') + 1] in data.get('fail_exec_pods', [])):
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
                     "pods": {"items": [self.pod()]},
                     "exec": "Filesystem Size Used Avail Use% Mounted on\n/dev/disk 10G 3G 7G 30% /data space"}

    @staticmethod
    def pod(name="worker", containers=None):
        return {"metadata": {"name": name}, "status": {"phase": "Running"}, "spec": {
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






    def test_kdf_namespace_report_includes_unmounted_claims(self):
        self.data["pvc"]["items"].append({"metadata": {"name": "unused"}})
        result = self.run_tool("kdf")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout, "PVC Name | Pod Name | Filesystem | Size | Used | Avail | Mounted on\n"
                         "data | worker | /dev/disk | 10G | 3G | 7G | /data space\n"
                         "unused |  |  |  |  |  | \n")
        self.assertEqual(self.calls, [
            ["get", "pvc", "-o", "json"], ["get", "pods", "-o", "json"],
            ["exec", "worker", "-c", "app", "--", "df", "-P", "-h", "--", "/data space"]])

    def test_kdf_all_running_pods_and_only_running_container_mounts(self):
        pods = [self.pod(name) for name in ("worker", "other", "pending", "done", "unknown")]
        for pod, phase in zip(pods, ("Running", "Running", "Pending", "Succeeded", None)):
            pod["status"]["phase"] = phase
        pods[0]["spec"]["containers"].append({"name": "sidecar", "volumeMounts": [
            {"name": "storage", "mountPath": "/data space"},
            {"name": "storage", "mountPath": "/second"}]})
        pods[0]["status"]["containerStatuses"] = [
            {"name": "app", "state": {"terminated": {}}},
            {"name": "sidecar", "state": {"running": {}}}]
        self.data["pods"]["items"] = pods
        result = self.run_tool("kdf")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual([call[1:4] for call in self.calls if "exec" in call],
                         [["other", "-c", "app"], ["worker", "-c", "sidecar"], ["worker", "-c", "sidecar"]])
        self.assertEqual(result.stdout.count("PVC Name |"), 1)
        self.assertEqual(len(result.stdout.splitlines()), 4)

    def test_kdf_block_claim_has_only_blank_row(self):
        self.data["pvc"]["items"][0]["spec"] = {"volumeMode": "Block"}
        result = self.run_tool("kdf")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout.splitlines()[1], "data |  |  |  |  |  | ")
        self.assertEqual(len(self.calls), 2)

    def test_kdf_namespace_and_cli_validation(self):
        for option in ("-n", "--namespace"):
            result = self.run_tool("kdf", option, "team-a")
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertTrue(all(call[:2] == ["-n", "team-a"] for call in self.calls))
        for args in [("--help",), ("-h",)]:
            result = self.run_tool("kdf", *args)
            self.assertEqual(result.returncode, 0)
            self.assertEqual(self.calls, [])
        for args in [("data",), ("--pod", "worker"), ("-c", "app"), ("--container", "app"),
                     ("-n",), ("-n", ""), ("-n", "bad name"), ("-n", "--all-namespaces"),
                     ("-n", "bad/name"), ("--bad",), ("--help", "extra"), ("-n", "a", "-n", "b")]:
            with self.subTest(args=args):
                result = self.run_tool("kdf", *args)
                self.assertEqual(result.returncode, 2, result.stderr)
                self.assertEqual(self.calls, [])

    def test_kdf_missing_null_fields_and_no_running_mount(self):
        for pvcs in ({}, {"items": None}, {"items": []}):
            self.data["pvc"] = pvcs
            result = self.run_tool("kdf")
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(len(result.stdout.splitlines()), 1)
        self.data["pvc"] = {"items": [{"metadata": {"name": "data"}}, {"metadata": None}]}
        for pods in ({}, {"items": None}, {"items": [self.pod(containers=[])]},
                     {"items": [{"status": {"phase": "Running"}, "spec": None}]},
                     {"items": [dict(self.pod(), status={"phase": "Running", "containerStatuses": []})]}):
            self.data["pods"] = pods
            result = self.run_tool("kdf")
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(result.stdout.splitlines()[1], "data |  |  |  |  |  | ")
            self.assertEqual(len(self.calls), 2)

    def test_kdf_invalid_mount_paths_fail_without_exec(self):
        for path in (None, "", 3, "relative", "/bad\x00path"):
            with self.subTest(path=path):
                self.data["pods"]["items"][0]["spec"]["containers"][0]["volumeMounts"][0]["mountPath"] = path
                result = self.run_tool("kdf")
                self.assertEqual(result.returncode, 1)
                self.assertIn("invalid mount", result.stderr)
                self.assertFalse(any("exec" in call for call in self.calls))

    def test_kdf_mount_aliases_dedup_and_quoted_argv(self):
        pod = self.data["pods"]["items"][0]
        pod["spec"]["volumes"].append({"name": "alias", "persistentVolumeClaim": {"claimName": "data"}})
        path = "/space 'quote' \\ dollar$ ; --"
        mounts = [{"name": "alias", "mountPath": path}, {"name": "storage", "mountPath": "/data space"}]
        pod["spec"]["containers"].append({"name": "sidecar", "volumeMounts": mounts})
        result = self.run_tool("kdf")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual([call[-1] for call in self.calls if "exec" in call], ["/data space", path])
        self.assertEqual(len(result.stdout.splitlines()), 3)

    def test_kdf_df_failure_continues_without_fabricated_usage(self):
        self.data["pods"]["items"] = [self.pod("aaa"), self.pod("zzz")]
        self.data["fail_exec_pods"] = ["aaa"]
        result = self.run_tool("kdf")
        self.assertEqual(result.returncode, 1)
        self.assertIn("df failed in pod aaa", result.stderr)
        self.assertIn("data | zzz | /dev/disk", result.stdout)
        self.assertNotIn("data | aaa", result.stdout)
        self.assertEqual(len(self.calls), 4)
        self.data.pop("fail_exec_pods")
        for output in ("", "Filesystem Size Used Avail Use% Mounted on", "not df output"):
            self.data["exec"] = output
            result = self.run_tool("kdf")
            self.assertEqual(result.returncode, 1)
            self.assertIn("invalid df output", result.stderr)
            self.assertEqual(len(result.stdout.splitlines()), 1)
            self.assertEqual(len(self.calls), 4)

    def test_kdf_df_parses_numeric_columns_not_whitespace_count(self):
        self.data["exec"] = "Filesystem Size Used Avail Capacity Mounted on\nserver:/fs name   1.5T 512G 1T 34% /mount with  spaces"
        result = self.run_tool("kdf")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout.splitlines()[1],
                         "data | worker | server:/fs name | 1.5T | 512G | 1T | /mount with  spaces")

    def test_kdf_large_namespace_snapshot_uses_no_argv_payload(self):
        self.data["pods"]["items"][0]["metadata"]["annotations"] = {"large": "x" * 150000}
        result = self.run_tool("kdf")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("data | worker | /dev/disk", result.stdout)
        self.assertEqual(len(self.calls), 3)

    def test_kdf_numeric_text_in_mountpoint_is_not_usage(self):
        self.data["exec"] = "Filesystem Size Used Avail Use% Mounted on\n/dev/disk 10G 3G 7G 30% /mount 1G 2G 3G 4% /tail"
        result = self.run_tool("kdf")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout.splitlines()[1],
                         "data | worker | /dev/disk | 10G | 3G | 7G | /mount 1G 2G 3G 4% /tail")

    def test_kdf_distinct_claims_match_volume_names_not_claim_names(self):
        self.data["pvc"]["items"].append({"metadata": {"name": "aaa"}})
        other = self.pod("aaa-worker")
        other["spec"]["volumes"][0]["persistentVolumeClaim"]["claimName"] = "aaa"
        self.data["pods"]["items"].append(other)
        result = self.run_tool("kdf")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual([line.split(" | ")[:2] for line in result.stdout.splitlines()[1:]],
                         [["aaa", "aaa-worker"], ["data", "worker"]])
        self.assertEqual(len(self.calls), 4)

    def test_kdf_nonrunning_pods_and_nonregular_mounts_get_blank_rows(self):
        for phase in ("Pending", "Succeeded", "Failed", None):
            self.data["pods"]["items"][0]["status"]["phase"] = phase
            result = self.run_tool("kdf")
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(result.stdout.splitlines()[1], "data |  |  |  |  |  | ")
            self.assertEqual(len(self.calls), 2)
        pod = self.pod()
        pod["spec"]["initContainers"] = pod["spec"]["containers"]
        pod["spec"]["ephemeralContainers"] = pod["spec"]["containers"]
        pod["spec"]["containers"] = [{"name": "app", "volumeMounts": None,
                                      "volumeDevices": [{"name": "storage", "devicePath": "/dev/raw"}]}]
        self.data["pods"]["items"] = [pod]
        result = self.run_tool("kdf")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout.splitlines()[1], "data |  |  |  |  |  | ")
        self.assertEqual(len(self.calls), 2)

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



    def test_cluster_and_exec_errors_normalized_to_one(self):
        for tool, resource, expected_calls in (("kdf", "pvc", 1), ("kdf", "pods", 2),
                                                ("kdf", "exec", 3), ("ocprems", "apirequestcounts", 1)):
            with self.subTest(tool=tool, resource=resource):
                self.data["fail"] = resource
                result = self.run_tool(tool)
                self.assertEqual(result.returncode, 1)
                self.assertIn("fixture: forbidden or command failed", result.stderr)
                self.assertEqual(len(self.calls), expected_calls)



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
