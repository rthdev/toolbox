"""Offline ocmt integration tests using a fixture-backed oc executable."""

from __future__ import annotations

import json
import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
TOOL = ROOT / "openshift" / "ocmt"


def container(name="app", cpu="500m", memory="1048576", **extra):
    return dict(
        name=name,
        resources={
            "requests": {"cpu": cpu, "memory": memory},
            "limits": {"cpu": "2", "memory": "1G"},
        },
        **extra,
    )


def pod(name="app", namespace="team", node="worker-a"):
    return {
        "metadata": {"name": name, "namespace": namespace},
        "spec": {"nodeName": node, "containers": [container()]},
        "status": {"phase": "Running"},
    }


def node(name="worker-a", cpu="4", memory="8Gi", labels=None):
    return {
        "metadata": {"name": name, "labels": labels or {}},
        "spec": {},
        "status": {
            "allocatable": {"cpu": cpu, "memory": memory},
            "conditions": [{"type": "Ready", "status": "True"}],
        },
    }


def table_rows(output):
    """Read the plain markdown table, ignoring headings and separators."""
    return [
        [cell.strip() for cell in line.strip().strip("|").split("|")]
        for line in output.splitlines()
        if line.startswith("|") and not line.startswith("| ---")
    ]


class OcmtTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(dir=os.environ.get("TMPDIR"))
        self.addCleanup(self.tmp.cleanup)
        self.directory = Path(self.tmp.name)
        self.log = self.directory / "calls.jsonl"
        self.fixture = self.directory / "fixture.json"
        self.env = dict(
            os.environ,
            PATH=str(self.directory) + os.pathsep + os.environ["PATH"],
            OCMT_LOG=str(self.log),
            OCMT_FIXTURE=str(self.fixture),
        )
        fake = self.directory / "oc"
        fake.write_text(
            """#!"""
            + sys.executable
            + """
import json, os, sys, time
from pathlib import Path
args = sys.argv[1:]
with open(os.environ['OCMT_LOG'], 'a') as log:
    log.write(json.dumps(args) + '\\n')
data = json.loads(Path(os.environ['OCMT_FIXTURE']).read_text())
if 'config' in args:
    key = 'namespace'
elif '--raw' in args:
    key = 'node_metrics' if args[args.index('--raw') + 1].endswith('/nodes') else 'pod_metrics'
else:
    key = args[args.index('get') + 1]
if data.get('sleep') == key:
    time.sleep(3)
if data.get('fail') == key:
    print('fixture: forbidden', file=sys.stderr)
    sys.exit(7)
value = data[key]
if key == 'pods' and isinstance(value, dict) and isinstance(value.get('items'), list):
    namespace = next((arg.split('=', 1)[1] for arg in args if arg.startswith('--namespace=')), None)
    if namespace:
        value = dict(value, items=[item for item in value['items']
                                  if not isinstance(item, dict) or 'metadata' not in item
                                  or item['metadata'].get('namespace') == namespace])
if key == 'nodes' and isinstance(value, dict) and '--selector=zone=east' in args:
    value = dict(value, items=[item for item in value['items']
                              if item['metadata'].get('labels', {}).get('zone') == 'east'])
print(value if isinstance(value, str) else json.dumps(value))
"""
        )
        fake.chmod(0o755)
        self.data = {
            "namespace": "team",
            "pods": {"items": [pod()]},
            "nodes": {"items": [node()]},
            "pod_metrics": {
                "items": [
                    {
                        "metadata": {"name": "app", "namespace": "team"},
                        "containers": [
                            {"name": "app", "usage": {"cpu": "125000000n", "memory": "1024Ki"}}
                        ],
                    }
                ]
            },
            "node_metrics": {
                "items": [
                    {"metadata": {"name": "worker-a"}, "usage": {"cpu": "1", "memory": "2Gi"}}
                ]
            },
        }

    def run_tool(self, *args):
        self.fixture.write_text(json.dumps(self.data))
        self.log.write_text("")
        result = subprocess.run(
            [sys.executable, str(TOOL), *args],
            env=self.env,
            capture_output=True,
            text=True,
            timeout=15,
        )
        self.calls = [json.loads(line) for line in self.log.read_text().splitlines()]
        return result

    def assert_table_alignment(self, output):
        """Check visible padding, including headers, totals and unknown cells."""
        lines = re.sub(r"\x1b\[[0-9;]*m", "", output).splitlines()
        text_columns = {"NAMESPACE", "POD", "NODE", "STATE", "ROLE",
                        "WORST_CPU_NODE", "WORST_MEM_NODE"}
        tables = 0
        headers = []
        widths = []
        for index, line in enumerate(lines):
            if not line.startswith("| "):
                continue
            fields = line[2:-2].split(" | ")
            if fields[0].strip() in {"NAMESPACE", "POD", "NODE", "ROLE"}:
                headers = [field.strip() for field in fields]
                widths = [len(field) for field in fields]
                self.assertEqual(
                    lines[index + 1],
                    "| " + " | ".join("-" * width for width in widths) + " |",
                )
                tables += 1
                is_header = True
            elif all(set(field) == {"-"} for field in fields):
                continue
            else:
                is_header = False
            self.assertEqual(len(fields), len(headers))
            for header, field, width in zip(headers, fields, widths):
                value = field.strip()
                if header in text_columns:
                    self.assertEqual(field, value.ljust(width), header)
                else:
                    self.assertEqual(field, value.rjust(width), header)
                    if not is_header and value != "unknown":
                        self.assertRegex(value, r"^-?\d+\.\d{2}$")
        self.assertGreater(tables, 0)

    def test_all_reports_right_align_numeric_columns_and_left_align_text(self):
        long_node = "worker-with-a-long-name"
        self.data["nodes"]["items"].append(
            node(long_node, "1234567890123", "123456789012345Mi")
        )
        large = pod("pod-with-a-long-name", node=long_node)
        large["spec"]["containers"] = [
            container(cpu="1234567890123", memory="123456789012345Mi")
        ]
        self.data["pods"]["items"].append(large)
        for action in ("capacity", "nstop", "ptop", "free"):
            with self.subTest(action=action):
                result = self.run_tool(action)
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertNotIn("\x1b", result.stdout)
                self.assert_table_alignment(result.stdout)

    def test_nstop_current_namespace_quantities_and_totals(self):
        result = self.run_tool("nstop")
        self.assertEqual(result.returncode, 0, result.stderr)
        rows = table_rows(result.stdout)
        self.assertEqual(
            rows[1], ["app", "worker-a", "0.50", "1.00", "2.00", "953.67", "0.12", "1.00"]
        )
        self.assertEqual(rows[2], ["TOTAL", "", "0.50", "1.00", "2.00", "953.67", "0.12", "1.00"])
        self.assertEqual(len(self.calls), 3)
        self.assertTrue(any("/namespaces/team/pods" in " ".join(call) for call in self.calls))
        self.assertTrue(any("--namespace=team" in call for call in self.calls))
        self.assertNotIn("\x1b", result.stdout)

    def test_effective_resources_include_ordered_sidecars_init_and_overhead(self):
        spec = self.data["pods"]["items"][0]["spec"]
        spec["containers"].append(container("second", "250m", "1M"))
        spec["initContainers"] = [
            container("sidecar", "100m", "1Mi", restartPolicy="Always"),
            container("setup", "2", "4Mi"),
            container("late-sidecar", "200m", "1Mi", restartPolicy="Always"),
        ]
        spec["ephemeralContainers"] = [container("debug", "100", "100Gi")]
        spec["overhead"] = {"cpu": "50m", "memory": "1Mi"}
        result = self.run_tool("nstop", "-n", "team")
        self.assertEqual(result.returncode, 0, result.stderr)
        row = table_rows(result.stdout)[1]
        self.assertEqual(row[2:6], ["2.15", "6.00", "8.05", "3815.70"])
        # Pod-level budgets override per-container aggregation for that resource.
        spec["resources"] = {"requests": {"cpu": "3"}, "limits": {"memory": "5Gi"}}
        result = self.run_tool("nstop", "-n", "team")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(table_rows(result.stdout)[1][2:6], ["3.05", "6.00", "8.05", "5121.00"])

    def test_ordinary_init_before_sidecar_does_not_include_later_sidecar(self):
        spec = self.data["pods"]["items"][0]["spec"]
        spec["containers"] = [container("app", "1", "2Mi")]
        spec["initContainers"] = [
            container("early-setup", "6", "12Mi"),
            container("sidecar", "3", "6Mi", restartPolicy="Always"),
        ]
        for item in spec["containers"] + spec["initContainers"]:
            item["resources"]["limits"] = dict(item["resources"]["requests"])
        result = self.run_tool("nstop", "-n", "team")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(table_rows(result.stdout)[1][2:6], ["6.00", "12.00", "6.00", "12.00"])

    def test_sidecar_dominated_steady_state_exceeds_init_peaks(self):
        spec = self.data["pods"]["items"][0]["spec"]
        spec["containers"] = [container("app", "2", "4Mi")]
        spec["initContainers"] = [
            container("early-setup", "1", "2Mi"),
            container("sidecar", "3", "6Mi", restartPolicy="Always"),
            container("late-sidecar", "4", "8Mi", restartPolicy="Always"),
        ]
        for item in spec["containers"] + spec["initContainers"]:
            item["resources"]["limits"] = dict(item["resources"]["requests"])
        result = self.run_tool("nstop", "-n", "team")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(table_rows(result.stdout)[1][2:6], ["9.00", "18.00", "9.00", "18.00"])

    def test_optional_resource_objects_accept_absent_null_and_empty(self):
        for value in (None, {}):
            for nested in (False, True):
                with self.subTest(value=value, nested=nested):
                    empty = pod()
                    spec = empty["spec"]
                    spec["initContainers"] = [container("setup")]
                    resources = {"requests": value, "limits": value} if nested else value
                    for item in spec["containers"] + spec["initContainers"]:
                        item["resources"] = resources
                    spec["resources"] = resources
                    spec["overhead"] = value
                    self.data["pods"]["items"] = [empty]
                    result = self.run_tool("nstop", "-n", "team")
                    self.assertEqual(result.returncode, 0, result.stderr)
                    self.assertEqual(table_rows(result.stdout)[1][2:6], ["0.00"] * 4)
        for item in spec["containers"] + spec["initContainers"]:
            item.pop("resources")
        spec.pop("resources")
        spec.pop("overhead")
        result = self.run_tool("nstop", "-n", "team")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(table_rows(result.stdout)[1][2:6], ["0.00"] * 4)

    def test_falsey_resource_mappings_fail_without_a_report(self):
        paths = [
            ("containers", 0, "resources"),
            ("initContainers", 0, "resources"),
            ("resources",),
            ("overhead",),
        ]
        paths += [
            path + (kind,)
            for path in paths[:3]
            for kind in ("requests", "limits")
        ]
        for path in paths:
            for value in ([], "", 0, False):
                for action in ("nstop", "ptop", "capacity", "free"):
                    with self.subTest(path=path, value=value, action=action):
                        malformed = pod("malformed")
                        spec = malformed["spec"]
                        spec["initContainers"] = [container("setup")]
                        spec["resources"] = {}
                        target = spec
                        for key in path[:-1]:
                            target = target[key]
                        target[path[-1]] = value
                        # A preceding valid pod must not produce a partial report.
                        self.data["pods"]["items"] = [pod(), malformed]
                        result = self.run_tool(action)
                        self.assertEqual(result.returncode, 1, result.stdout)
                        self.assertEqual(result.stdout, "")
                        self.assertIn("expected a JSON object", result.stderr)
                        self.assertNotIn("Traceback", result.stderr)

    def test_terminal_pod_usage_is_zero_in_rows_and_totals(self):
        for phase in ("Succeeded", "Failed"):
            for stale_metrics in (False, True):
                for active_phase in (None, "measured", "Running", "Pending", "Unknown"):
                    with self.subTest(
                        phase=phase, stale_metrics=stale_metrics, active_phase=active_phase
                    ):
                        terminal = pod("completed")
                        terminal["status"]["phase"] = phase
                        self.data["pods"]["items"] = [terminal]
                        self.data["pod_metrics"]["items"] = []
                        if stale_metrics:
                            self.data["pod_metrics"]["items"].append({
                                "metadata": terminal["metadata"],
                                "containers": [{
                                    "name": "app", "usage": {"cpu": "9", "memory": "9Gi"}
                                }],
                            })
                        if active_phase is not None:
                            active = pod()
                            if active_phase != "measured":
                                active["status"]["phase"] = active_phase
                            self.data["pods"]["items"].append(active)
                            if active_phase == "measured":
                                self.data["pod_metrics"]["items"].append({
                                    "metadata": active["metadata"],
                                    "containers": [{
                                        "name": "app", "usage": {"cpu": "250m", "memory": "2Mi"}
                                    }],
                                })
                        result = self.run_tool("nstop", "-n", "team")
                        self.assertEqual(result.returncode, 0, result.stderr)
                        rows = {row[0]: row for row in table_rows(result.stdout)[1:]}
                        self.assertEqual(rows["completed"][-2:], ["0.00", "0.00"])
                        # Terminal usage must not change declared spec requests/limits.
                        self.assertEqual(
                            rows["completed"][2:6], ["0.50", "1.00", "2.00", "953.67"]
                        )
                        expected = ["0.00", "0.00"] if active_phase is None else (
                            ["0.25", "2.00"] if active_phase == "measured"
                            else ["unknown", "unknown"]
                        )
                        self.assertEqual(rows["TOTAL"][-2:], expected)
                        if active_phase is not None:
                            self.assertEqual(rows["app"][-2:], expected)
                        self.assertEqual(len(self.calls), 2)

    def test_missing_metrics_remain_unknown_in_rows_and_totals(self):
        self.data["pods"]["items"].append(pod("unmeasured"))
        for metrics in (
            {"items": []},
            self.data["pod_metrics"],
            {
                "items": [
                    {
                        "metadata": {"name": "app", "namespace": "team"},
                        "containers": [{"name": "app", "usage": {"cpu": "0"}}],
                    }
                ]
            },
        ):
            self.data["pod_metrics"] = metrics
            result = self.run_tool("nstop", "-n", "team")
            self.assertEqual(result.returncode, 0, result.stderr)
            rows = table_rows(result.stdout)
            self.assertEqual(rows[-1][-2:], ["unknown", "unknown"])
            self.assertEqual(
                next(row for row in rows if row[0] == "unmeasured")[-2:], ["unknown", "unknown"]
            )
        self.data["fail"] = "pod_metrics"
        result = self.run_tool("nstop", "-n", "team")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("metrics unavailable", result.stderr)
        self.assertIn("fixture: forbidden", result.stderr)
        self.assertEqual(table_rows(result.stdout)[-1][-2:], ["unknown", "unknown"])

    def test_ptop_bulk_role_filter_sort_limit_and_namespace_identity(self):
        self.data["nodes"]["items"][0]["metadata"]["labels"]["zone"] = "east"
        self.data["pods"]["items"] = [
            pod("app", "team"),
            pod("app", "other"),
            pod("pending", node=""),
            pod("control", node="master"),
        ]
        self.data["nodes"]["items"].append(
            node(
                "master",
                labels={"node-role.kubernetes.io/master": "", "node-role.kubernetes.io/worker": ""},
            )
        )
        self.data["pod_metrics"]["items"].append(
            {
                "metadata": {"name": "app", "namespace": "other"},
                "containers": [{"name": "app", "usage": {"cpu": "2", "memory": "1Mi"}}],
            }
        )
        result = self.run_tool(
            "ptop",
            "--node-role",
            "worker",
            "--label",
            "zone=east",
            "--sort-by",
            "CPU_USAGE",
            "--limit",
            "1",
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        rows = table_rows(result.stdout)
        self.assertEqual(len(rows), 2)
        self.assertEqual(rows[1][:3], ["other", "app", "worker-a"])
        self.assertEqual(rows[1][-2:], ["2.00", "1.00"])
        self.assertEqual(len(self.calls), 3)
        self.assertTrue(any("-A" in call for call in self.calls))
        self.assertTrue(any("--selector=zone=east" in call for call in self.calls))
        result = self.run_tool("ptop", "--sort-by", "CPU_USAGE")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertNotIn("pending", result.stdout)
        self.assertEqual(len(self.calls), 2)  # no node fetch without a node filter
        self.assertEqual(table_rows(result.stdout)[-1][1], "control")
        result = self.run_tool("nstop", "-n", "team", "--node-role", "control")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(table_rows(result.stdout)[1][0], "control")

    def test_capacity_groups_roles_and_counts_only_assigned_nonterminal_pods(self):
        self.data["nodes"]["items"].extend(
            [
                node(
                    "infra-a",
                    labels={
                        "node-role.kubernetes.io/infra": "",
                        "node-role.kubernetes.io/worker": "",
                    },
                ),
                node("master", labels={"node-role.kubernetes.io/control-plane": ""}),
            ]
        )
        pods = self.data["pods"]["items"]
        pods.extend([pod("pending", node=""), pod("done"), pod("failed"), pod("terminating")])
        pods[2]["status"]["phase"] = "Succeeded"
        pods[3]["status"]["phase"] = "Failed"
        pods[4]["metadata"]["deletionTimestamp"] = "2026-01-01T00:00:00Z"
        result = self.run_tool()
        self.assertEqual(result.returncode, 0, result.stderr)
        rows = table_rows(result.stdout)
        worker = next(row for row in rows if row[0] == "worker-a")
        self.assertEqual(
            worker,
            [
                "worker-a",
                "Ready",
                "4.00",
                "8192.00",
                "1.00",
                "2.00",
                "4.00",
                "1907.35",
                "1.00",
                "2048.00",
            ],
        )
        infra = next(row for row in rows if row[0] == "infra-a")
        self.assertEqual(infra[-2:], ["unknown", "unknown"])
        self.assertIn("Node role: CONTROL", result.stdout)
        self.assertIn("Node role: INFRA", result.stdout)
        self.assertEqual(len(self.calls), 3)
        self.assertFalse(any("describe" in call or "top" in call for call in self.calls))
        self.assertTrue(any("pods" in call and "-A" in call for call in self.calls))

    def test_free_uses_one_table_with_shared_widths_across_roles(self):
        control = "control-with-a-long-cpu-node-name"
        infra = "infra-with-an-even-longer-memory-node-name"
        self.data["nodes"]["items"] = [
            node("worker-a"),
            node(infra, "1234567890123", "123456789012345Mi",
                 labels={"node-role.kubernetes.io/infra": "", "zone": "east"}),
            node(control, "8", "16Gi",
                 labels={"node-role.kubernetes.io/control-plane": "", "zone": "east"}),
        ]
        headers = [
            "ROLE", "CPU_ALLOC", "CPU_REQUEST", "CPU_EFF(N-1)", "CPU_FREE",
            "MEM_ALLOC(Mi)", "MEM_REQUEST(Mi)", "MEM_EFF(N-1)(Mi)", "MEM_FREE(Mi)",
            "WORST_CPU_NODE", "WORST_MEM_NODE",
        ]
        expected = [
            ["control", "8.00", "0.00", "0.00", "0.00", "16384.00", "0.00",
             "0.00", "0.00", control, control],
            ["infra", "1234567890123.00", "0.00", "0.00", "0.00",
             "123456789012345.00", "0.00", "0.00", "0.00", infra, infra],
            ["worker", "4.00", "0.50", "0.00", "-0.50", "8192.00", "1.00",
             "0.00", "-1.00", "worker-a", "worker-a"],
        ]
        for arguments, selected in (
            ((), expected),
            (("--label", "zone=east"), expected[:2]),
            (("--node-role", "worker"), expected[2:]),
            (("--label", "zone=east", "--node-role", "worker"), []),
        ):
            with self.subTest(arguments=arguments):
                result = self.run_tool("free", *arguments)
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertEqual(result.stderr, "")
                self.assertEqual(len(self.calls), 2)
                if not selected:
                    self.assertEqual(result.stdout, "No matching nodes found.\n")
                    continue
                self.assertEqual(result.stdout.count("| ROLE"), 1)
                self.assertEqual(table_rows(result.stdout), [headers] + selected)
                lines = [line for line in result.stdout.splitlines() if line.startswith("|")]
                self.assertEqual(len(lines), len(selected) + 2)
                boundaries = [index for index, char in enumerate(lines[0]) if char == "|"]
                for line in lines[1:]:
                    self.assertEqual(
                        [index for index, char in enumerate(line) if char == "|"], boundaries
                    )
                self.assert_table_alignment(result.stdout)
                self.assertIn("N-1 aggregate headroom: not a scheduling guarantee.\n", result.stdout)
                self.assertIn(
                    "Allocatable includes only Ready, uncordoned nodes; "
                    "requests include all matching nodes.\n", result.stdout
                )
        self.data["nodes"]["items"] = []
        result = self.run_tool("free")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout, "No matching nodes found.\n")

    def test_free_reports_conservative_n_minus_one_arithmetic_not_guarantee(self):
        self.data["nodes"]["items"].extend(
            [
                node("worker-b", "8", "4Gi"),
                node("cordoned", "100", "1Ti"),
                node("not-ready", "100", "1Ti"),
            ]
        )
        self.data["nodes"]["items"][2]["spec"]["unschedulable"] = True
        self.data["nodes"]["items"][3]["status"]["conditions"] = []
        self.data["pods"]["items"].append(pod("evacuate", node="cordoned"))
        result = self.run_tool("free")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(
            table_rows(result.stdout)[1],
            [
                "worker",
                "12.00",
                "1.00",
                "4.00",
                "3.00",
                "12288.00",
                "2.00",
                "4096.00",
                "4094.00",
                "worker-b",
                "worker-a",
            ],
        )
        self.assertIn("not a scheduling guarantee", result.stdout)
        self.assertNotIn("N-1 safe", result.stdout)
        self.assertEqual(len(self.calls), 2)
        self.data["nodes"]["items"] = [node()]
        result = self.run_tool("free")
        self.assertEqual(table_rows(result.stdout)[1][3:5], ["0.00", "-0.50"])
        self.data["nodes"]["items"][0]["spec"]["unschedulable"] = True
        result = self.run_tool("free")
        self.assertEqual(table_rows(result.stdout)[1][-2:], ["none", "none"])

    def test_discovery_failures_are_bounded_and_never_partial_reports(self):
        for value in (
            "not json",
            None,
            [],
            {},
            {"items": None},
            {"items": "bad"},
            {"items": [{}]},
            {"items": [None]},
        ):
            self.data["pods"] = value
            result = self.run_tool("nstop", "-n", "team")
            self.assertEqual(result.returncode, 1)
            self.assertEqual(result.stdout, "")
            self.assertIn("ocmt:", result.stderr)
            self.assertNotIn("Traceback", result.stderr)
        self.data["pods"] = {"items": [pod()]}
        self.data["fail"] = "pods"
        result = self.run_tool("nstop", "-n", "team")
        self.assertEqual(result.returncode, 1)
        self.assertIn("fixture: forbidden", result.stderr)
        self.assertNotIn("Traceback", result.stderr)
        self.data.pop("fail")
        self.data["sleep"] = "pods"
        result = self.run_tool("nstop", "-n", "team", "--timeout", "1")
        self.assertEqual(result.returncode, 1)
        self.assertIn("timed out", result.stderr)
        self.assertTrue(all("--request-timeout=1s" in call for call in self.calls))
        self.data["sleep"] = "pod_metrics"
        result = self.run_tool("nstop", "-n", "team", "--timeout", "1")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("metrics unavailable", result.stderr)
        self.assertEqual(table_rows(result.stdout)[1][-2:], ["unknown", "unknown"])
        (self.directory / "oc").unlink()
        self.env["PATH"] = str(self.directory)
        result = self.run_tool("nstop")
        self.assertEqual(result.returncode, 1)
        self.assertIn("oc", result.stderr)
        self.assertNotIn("Traceback", result.stderr)
        result = self.run_tool("--help")
        self.assertEqual(result.returncode, 0)
        self.assertEqual(self.calls, [])

    def test_invalid_cli_is_rejected_before_any_oc_call(self):
        for args in (
            ("--bad",),
            ("ptop", "--limit", "0"),
            ("ptop", "--limit", "-1"),
            ("--timeout", "0"),
            ("--timeout", "3601"),
            ("--timeout", "nan"),
            ("nstop", "-n", ""),
            ("nstop", "-n", "bad/name"),
            ("nstop", "-n", "A"),
            ("nstop", "-n", "x" * 64),
            ("nstop", "--sort-by", "NAMESPACE"),
            ("capacity", "-n", "team"),
            ("free", "--limit", "5"),
            ("capacity", "--sort-by", "CPU_USAGE"),
            ("nstop", "--label", ""),
            ("nstop", "--label", "zone=bad\nvalue"),
            ("ptop", "--sort-by", "invalid"),
        ):
            with self.subTest(args=args):
                result = self.run_tool(*args)
                self.assertEqual(result.returncode, 2, result.stderr)
                self.assertEqual(self.calls, [])
                self.assertNotIn("Traceback", result.stderr)
        self.data["namespace"] = ""
        result = self.run_tool("nstop")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertTrue(any("--namespace=default" in call for call in self.calls))
        self.data["namespace"] = "../bad"
        result = self.run_tool("nstop")
        self.assertEqual(result.returncode, 1)
        self.assertEqual(len(self.calls), 1)

    def test_quantity_units_and_invalid_values(self):
        import runpy

        quantity = runpy.run_path(str(TOOL))["quantity"]
        for value, resource, expected in (
            ("1E", "cpu", 1e18),
            ("1Ei", "memory", 1099511627776),
            ("1Pi", "memory", 1073741824),
            ("1Ti", "memory", 1048576),
            ("1Gi", "memory", 1024),
            ("1Mi", "memory", 1),
            ("1024Ki", "memory", 1),
            ("1G", "memory", 953.67431640625),
            ("1M", "memory", 0.95367431640625),
            ("1k", "memory", 0.00095367431640625),
            ("1048576", "memory", 1),
            ("400m", "memory", 0.4 / 1048576),
            ("250m", "cpu", 0.25),
            ("125000u", "cpu", 0.125),
            ("125000000n", "cpu", 0.125),
            ("1e3", "cpu", 1000),
            ("1E-3", "cpu", 0.001),
            (".5", "cpu", 0.5),
        ):
            with self.subTest(value=value, resource=resource):
                self.assertAlmostEqual(quantity(value, resource), expected)
        for value in ("NaN", "inf", "1e9999999", "-1", "1MiB", "", "1.2.3", None, True):
            with self.subTest(value=value):
                with self.assertRaises(ValueError):
                    quantity(value, "memory")
        self.data["pods"]["items"][0]["spec"]["containers"][0]["resources"]["requests"][
            "memory"
        ] = "1e9999999"
        result = self.run_tool("nstop", "-n", "team")
        self.assertEqual(result.returncode, 1)
        self.assertNotIn("Traceback", result.stderr)
        self.assertEqual(result.stdout, "")

    def test_partial_container_metrics_are_not_a_complete_pod_total(self):
        spec = self.data["pods"]["items"][0]["spec"]
        spec["containers"].append(container("second"))
        result = self.run_tool("nstop", "-n", "team")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(table_rows(result.stdout)[1][-2:], ["unknown", "unknown"])
        self.data["pod_metrics"]["items"][0]["containers"].append(
            {"name": "second", "usage": {"cpu": "375m", "memory": "3Mi"}}
        )
        result = self.run_tool("nstop", "-n", "team")
        self.assertEqual(table_rows(result.stdout)[1][-2:], ["0.50", "4.00"])
        spec["initContainers"] = [container("sidecar", restartPolicy="Always")]
        result = self.run_tool("nstop", "-n", "team")
        self.assertEqual(table_rows(result.stdout)[1][-2:], ["unknown", "unknown"])

    def test_capacity_warning_colors_are_tty_only_and_honor_no_color(self):
        import pty

        self.data["pods"]["items"][0]["spec"]["containers"][0]["resources"]["requests"]["cpu"] = "3"
        self.fixture.write_text(json.dumps(self.data))
        for term, no_color, expected in (
            ("xterm", False, True),
            ("xterm", True, False),
            ("dumb", False, False),
            ("", False, False),
        ):
            env = dict(self.env, TERM=term)
            env.pop("NO_COLOR", None)
            if no_color:
                env["NO_COLOR"] = ""
            master, slave = pty.openpty()
            try:
                result = subprocess.run(
                    [sys.executable, str(TOOL)],
                    env=env,
                    stdout=slave,
                    stderr=subprocess.PIPE,
                    timeout=10,
                )
                self.assertEqual(result.returncode, 0, result.stderr)
                output = os.read(master, 65536).decode()
            finally:
                os.close(master)
                os.close(slave)
            self.assertEqual("\x1b[91m3.00\x1b[0m" in output, expected)
            self.assert_table_alignment(output)
        result = self.run_tool()
        self.assertNotIn("\x1b", result.stdout)

    def test_large_snapshot_keeps_constant_command_count_and_exact_aggregation(self):
        self.data["pods"]["items"] = [pod(f"app-{index:05}") for index in range(10000)]
        self.data["pods"]["items"][0]["metadata"]["annotations"] = {"large": "x" * 150000}
        result = self.run_tool("capacity")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(len(self.calls), 3)
        self.assertEqual(table_rows(result.stdout)[1][4:6], ["5000.00", "10000.00"])
        result = self.run_tool("ptop", "--limit", "3")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(len(self.calls), 2)
        self.assertEqual(
            [row[1] for row in table_rows(result.stdout)[1:]],
            ["app-00000", "app-00001", "app-00002"],
        )
        self.assertTrue(all(len(json.dumps(call)) < 300 for call in self.calls))

    def test_empty_reports_and_unset_resources(self):
        for action in ("nstop", "ptop", "capacity", "free"):
            self.data["pods"] = {"items": []}
            self.data["nodes"] = {"items": []}
            result = self.run_tool(action)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn("No ", result.stdout)
        empty = pod()
        empty["spec"]["containers"][0]["resources"] = {}
        empty["spec"]["overhead"] = {"cpu": "10m", "memory": "1Mi"}
        self.data["pods"] = {"items": [empty]}
        result = self.run_tool("nstop", "-n", "team")
        self.assertEqual(table_rows(result.stdout)[1][2:6], ["0.01", "1.00", "0.00", "0.00"])

    def test_namespace_override_and_label_selection_are_effective(self):
        self.data["pods"]["items"].append(pod("other-pod", "other"))
        result = self.run_tool("ptop", "-n", "other")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(table_rows(result.stdout)[1][:2], ["other", "other-pod"])
        self.assertEqual(len(table_rows(result.stdout)), 2)
        self.assertFalse(any("config" in call or "-A" in call for call in self.calls))
        self.data["nodes"]["items"].append(node("east", labels={"zone": "east"}))
        for action in ("capacity", "free"):
            result = self.run_tool(action, "--label", "zone=east")
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertNotIn("worker-a", result.stdout)
            self.assertIn("east", result.stdout)

    def test_standalone_copy_and_metrics_endpoint_failures(self):
        import shutil

        copied = self.directory / "ocmt"
        shutil.copy2(TOOL, copied)
        self.fixture.write_text(json.dumps(self.data))
        result = subprocess.run(
            [sys.executable, str(copied), "free"],
            cwd=self.directory,
            env=self.env,
            text=True,
            capture_output=True,
            timeout=10,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("not a scheduling guarantee", result.stdout)
        for value in ("not JSON", {"items": None}, {"items": []}):
            self.data["node_metrics"] = value
            result = self.run_tool("capacity")
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(table_rows(result.stdout)[-1][-2:], ["unknown", "unknown"])
        self.data["fail"] = "node_metrics"
        result = self.run_tool("capacity")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("metrics unavailable", result.stderr)
        self.data["fail"] = "nodes"
        result = self.run_tool("capacity")
        self.assertEqual(result.returncode, 1)
        self.assertEqual(result.stdout, "")
        self.assertNotIn("Traceback", result.stderr)


if __name__ == "__main__":
    unittest.main()
