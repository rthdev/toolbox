"""Offline ogn integration tests using real Bash/jq and an isolated oc stub."""
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]


def node(name, roles=(), region="N/A", zone="N/A", cpu="8", memory="32Gi"):
    labels = {"node-role.kubernetes.io/" + role: "" for role in roles}
    labels.update({"topology.kubernetes.io/region": region, "topology.kubernetes.io/zone": zone})
    return {"metadata": {"name": name, "labels": labels},
            "status": {"capacity": {"cpu": cpu, "memory": memory}}}


class OgnTest(unittest.TestCase):
    def setUp(self):
        self.bash = shutil.which("bash")
        self.jq = shutil.which("jq")
        self.assertIsNotNone(self.bash)
        self.assertIsNotNone(self.jq)
        tmp = tempfile.TemporaryDirectory(dir=os.environ.get("TMPDIR"))
        self.addCleanup(tmp.cleanup)
        self.directory = Path(tmp.name)
        self.fixture = self.directory / "nodes.json"
        self.log = self.directory / "calls"
        self.env = dict(os.environ, PATH=str(self.directory), FIXTURE=str(self.fixture),
                        CALL_LOG=str(self.log), OC_STATUS="0")
        for name, target in (("jq", self.jq), ("awk", shutil.which("awk"))):
            (self.directory / name).symlink_to(target)
        self.stub("oc", f'''#!{self.bash}
printf '%s\\n' "$*" >> "$CALL_LOG"
while IFS= read -r line || [[ -n $line ]]; do printf '%s\\n' "$line"; done < "$FIXTURE"
if [[ $OC_STATUS != 0 ]]; then printf 'oc failed\\n' >&2; fi
exit "$OC_STATUS"
''')
        self.data = {"items": [node("worker", ["worker"])]}

    def stub(self, name, content):
        path = self.directory / name
        if path.is_symlink():
            path.unlink()
        path.write_text(content)
        path.chmod(0o755)

    def run_tool(self, *args, raw=None):
        self.fixture.write_text(json.dumps(self.data) + "\n" if raw is None else raw)
        self.log.write_text("")
        result = subprocess.run([self.bash, str(ROOT / "openshift/ogn"), *args],
                                env=self.env, capture_output=True, text=True, timeout=10)
        self.calls = self.log.read_text().splitlines()
        return result

    def assert_table(self, output, rows):
        table = [["NAME", "ROLES", "CPU", "MEMORY", "REGION", "ZONE"]] + rows
        widths = [max(len(row[column]) for row in table) for column in range(6)]
        expected = ["  ".join(value.rjust(widths[column]) if column in (2, 3)
                              else value.ljust(widths[column])
                              for column, value in enumerate(row)) for row in table]
        self.assertEqual(output, "\n".join(expected) + "\n")

    def test_missing_optional_fields_keep_na_and_blank_roles(self):
        self.data = {"items": [{"metadata": {"name": "unknown"}},
                                node("worker", ["worker"])]}
        result = self.run_tool()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assert_table(result.stdout, [["unknown", "", "N/A", "N/A", "N/A", "N/A"],
                                          ["worker", "worker", "8", "32Gi", "N/A", "N/A"]])

    def test_malformed_optional_values_are_not_silently_missing(self):
        for value in (False, [], "invalid", 0):
            for section in ("labels", "capacity"):
                with self.subTest(value=value, section=section):
                    bad = node("bad")
                    parent = "metadata" if section == "labels" else "status"
                    bad[parent][section] = value
                    self.data = {"items": [node("good"), bad]}
                    result = self.run_tool()
                    self.assertNotEqual(result.returncode, 0)
                    self.assertEqual(result.stdout, "")
        for key in ("cpu", "memory"):
            bad = node("bad")
            bad["status"]["capacity"][key] = False
            self.data = {"items": [bad]}
            result = self.run_tool()
            self.assertNotEqual(result.returncode, 0)
            self.assertEqual(result.stdout, "")

    def test_invalid_snapshots_never_look_like_empty_success(self):
        for raw in ("", "null\n", "{}\n", '{"items": {}}\n',
                    '{"items": []}\n{"items": []}\n',
                    json.dumps({"items": [node("valid"), node("bad|name")]}),
                    json.dumps({"items": [node("bad", cpu=[])]})):
            with self.subTest(raw=raw):
                result = self.run_tool(raw=raw)
                self.assertNotEqual(result.returncode, 0)
                self.assertEqual(result.stdout, "")
                self.assertTrue(result.stderr)

    def test_empty_node_list_prints_only_header(self):
        self.data = {"items": []}
        result = self.run_tool()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assert_table(result.stdout, [])

    def test_dynamic_widths_and_raw_capacity_right_alignment(self):
        long_name = "worker-" + "long" * 12
        self.data["items"] = [node(long_name, ["worker"], "region-" + "x" * 24, "zone-" + "y" * 24,
                                   "16000m", "123456789012Ki"),
                              node("a", ["master"], cpu="2", memory="4Gi")]
        result = self.run_tool()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assert_table(result.stdout, [["a", "master", "2", "4Gi", "N/A", "N/A"],
                                          [long_name, "worker", "16000m", "123456789012Ki",
                                           "region-" + "x" * 24, "zone-" + "y" * 24]])

    def test_topology_sort_has_deterministic_name_tiebreaker(self):
        nodes = [node("z", ["worker"], "a", "a"), node("a", ["worker"], "a", "a"),
                 node("region-b", ["worker"], "b", "a"), node("zone-b", ["worker"], "a", "b"),
                 node("master", ["master"], "z", "z"), node("infra", ["infra"], "z", "z")]
        for snapshot in (nodes, list(reversed(nodes))):
            self.data["items"] = snapshot
            result = self.run_tool()
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual([line.split()[0] for line in result.stdout.splitlines()[1:]],
                             ["master", "infra", "a", "z", "zone-b", "region-b"])

    def test_control_plane_alias_and_mixed_role_precedence(self):
        self.data["items"] = [node("worker", ["worker"]),
                              node("infra-worker", ["infra", "worker"]),
                              node("control", ["control-plane"], region="A"),
                              node("both", ["master", "control-plane", "infra", "worker"])]
        result = self.run_tool()
        self.assertEqual(result.returncode, 0, result.stderr)
        rows = [line.split() for line in result.stdout.splitlines()[1:]]
        self.assertEqual([row[0] for row in rows[:2]], ["control", "both"])
        self.assertEqual([row[1] for row in rows],
                         ["master", "master,infra,worker", "infra,worker", "worker"])
        self.assertEqual(self.calls, ["get nodes -o json"])

    def test_missing_dependencies_fail_before_oc_and_help_needs_none(self):
        for dependency in ("jq", "awk", "oc"):
            path = self.directory / dependency
            saved = self.directory / (dependency + ".saved")
            path.rename(saved)
            result = self.run_tool()
            self.assertNotEqual(result.returncode, 0)
            self.assertEqual(result.stdout, "")
            self.assertIn(dependency, result.stderr)
            self.assertEqual(self.calls, [])
            help_result = self.run_tool("--help")
            self.assertEqual(help_result.returncode, 0)
            saved.rename(path)

    def test_help_and_arguments_are_handled_before_cluster_access(self):
        for flag in ("--help", "-h"):
            result = self.run_tool(flag)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn("Usage:", result.stdout)
            self.assertEqual(self.calls, [])
        for args in (("--bad",), ("worker",), ("--help", "extra")):
            result = self.run_tool(*args)
            self.assertNotEqual(result.returncode, 0)
            self.assertEqual(result.stdout, "")
            self.assertEqual(self.calls, [])

    def test_jq_failure_never_prints_partial_report(self):
        for raw in ('{broken\n', '{"items": [null]}\n'):
            with self.subTest(raw=raw):
                result = self.run_tool(raw=raw)
                self.assertNotEqual(result.returncode, 0)
                self.assertEqual(result.stdout, "")
        self.stub("jq", f'#!{self.bash}\nprintf "partial row\\n"\nexit 9\n')
        result = self.run_tool()
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(result.stdout, "")

    def test_oc_failure_never_prints_partial_report(self):
        self.env["OC_STATUS"] = "7"
        result = self.run_tool()
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(result.stdout, "")
        self.assertIn("oc failed", result.stderr)
