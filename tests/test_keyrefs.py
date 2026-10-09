"""Offline contracts for the independently copyable drgn diagnostic."""
import importlib.machinery
import importlib.util
from pathlib import Path
import subprocess
import sys
import unittest

TOOL = Path(__file__).resolve().parents[1] / "linux/keyrefs"


def load_tool():
    loader = importlib.machinery.SourceFileLoader("keyrefs", str(TOOL))
    spec = importlib.util.spec_from_loader(loader.name, loader)
    module = importlib.util.module_from_spec(spec)
    loader.exec_module(module)
    return module


class CliTests(unittest.TestCase):
    def test_help_without_drgn_and_strict_serials(self):
        result = subprocess.run([sys.executable, "-S", str(TOOL), "--help"],
                                capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        tool = load_tool()
        self.assertEqual(tool.parse_args(["0x123", "123"]).serial, [0x123])
        for value in ("0", "80000000", "-1", "xyz", "000000001", " 123"):
            result = subprocess.run([sys.executable, str(TOOL), value],
                                    capture_output=True, text=True)
            self.assertEqual(result.returncode, 2, value)
        options = tool.parse_args(["--deep", "123"])
        self.assertTrue(options.maps and options.slab and options.files)
        self.assertFalse(options.reverse)
        self.assertTrue(tool.parse_args(["--reverse", "123"]).slab)


class EvidenceTests(unittest.TestCase):
    def test_deduplicated_edges_observations_and_secure_output(self):
        import os
        import tempfile
        tool = load_tool()
        report = tool.Report([0x123])
        report.add_key(0x123, 0x1000, "a\nkey", 1000, 4)
        for _ in range(2):
            report.observe(0x1000, ("cred", 0x2000, "session_keyring"),
                           {"kind": "process", "pid": 7, "path": "x\n\x1b[31m"})
        report.observe(0x1000, ("cred", 0x2000, "session_keyring"),
                       {"kind": "file", "pid": 8, "fd": 3})
        data = report.as_dict()
        self.assertEqual(data["keys"][0]["structural_refs"], 1)
        self.assertEqual(len(data["keys"][0]["holders"]), 2)
        text = tool.render(report, verbose=False)
        self.assertNotIn("\x1b", text)
        self.assertIn("not prove a leak", text)
        self.assertNotIn("0x2000", text)
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "report"
            tool.write_output(path, text)
            self.assertEqual(path.stat().st_mode & 0o777, 0o600)
            self.assertEqual(path.read_text(), text)
            with self.assertRaises(FileExistsError):
                tool.write_output(path, "overwrite")
            link = Path(directory) / "link"
            os.symlink(path, link)
            with self.assertRaises(FileExistsError):
                tool.write_output(link, "overwrite")
        self.assertEqual(report.exit_code, 0)
        report.warn("read fault")
        self.assertEqual(report.exit_code, 3)
        self.assertEqual(report.as_dict()["status"], "partial")


class Obj:
    def __init__(self, address=0, **fields):
        self.address = address
        self.__dict__.update(fields)

    def __int__(self):
        return self.address

    def __bool__(self):
        return bool(self.address)

    def string_(self):
        return self.text


def string(text):
    return Obj(text=text.encode())


def count(number):
    return Obj(counter=number)


class FakeBackend:
    def __init__(self):
        self.cred = Obj(0x2000, usage=count(2), session_keyring=0x1000,
                        process_keyring=0, thread_keyring=0, request_key_auth=0)
        self.file = Obj(0x3000, f_cred=self.cred, f_count=count(0), f_path="a\nb")
        self.key = Obj(0x1000, serial=0x123, description=string("key"), uid=Obj(val=5),
                       usage=count(3), type=0x99, keys=Obj(root=0), restrict_link=0)
        self.task = Obj(0x4000, cred=self.cred, real_cred=self.cred, pid=8, tgid=7,
                        comm=string("worker"), files=1, mm=1)
        self.pointer_size = 8
        self.byteorder = "little"

    def keys(self):
        return [self.key]

    def tasks(self):
        return [self.task]

    def files(self, task):
        return [(4, self.file)]

    def vmas(self, task):
        return [Obj(vm_file=self.file)]

    def allocated(self, name, type_name):
        return [self.cred] if name == "cred_jar" else [self.file]

    def path(self, file):
        return file.f_path

    def is_keyring(self, key):
        return True

    def object(self, type_name, address):
        return self.nodes[address]


class MovingPointer:
    """A memory-backed pointer whose slot changes after its first access."""

    def __init__(self, first, later):
        self.first, self.later = first, later
        self.accesses = 0

    def current(self):
        value = self.first if self.accesses == 0 else self.later
        self.accesses += 1
        return value

    def read_(self):
        return self.current()

    def __int__(self):
        return int(self.current())

    def __bool__(self):
        return bool(self.current())

    def __getattr__(self, name):
        return getattr(self.current(), name)


class CollectionTests(unittest.TestCase):
    def test_memory_backed_credentials_keep_address_and_fields_together(self):
        tool = load_tool()
        backend = FakeBackend()
        other = Obj(0x2100, usage=count(9), session_keyring=0,
                    process_keyring=0x1000, thread_keyring=0, request_key_auth=0)
        report = tool.Report([0x123])
        report.add_key(0x123, 0x1000, "key", 5, 1)
        collector = tool.Collector(backend, report, lambda _: None)
        collector.cred(MovingPointer(backend.cred, other), {"kind": "process"})
        self.assertEqual(report.edges[0x1000], {("cred", 0x2000, "session_keyring")})
        self.assertEqual(next(iter(report.holders[0x1000].values()))["cred_usage"], 2)

    def test_slab_credential_reverse_target_uses_the_observed_edge_address(self):
        tool = load_tool()
        backend = FakeBackend()
        other = Obj(0x2100, usage=count(9), session_keyring=0,
                    process_keyring=0x1000, thread_keyring=0, request_key_auth=0)
        report = tool.Report([0x123])
        report.add_key(0x123, 0x1000, "key", 5, 1)
        collector = tool.Collector(backend, report, lambda _: None)
        collector.slab_cred(MovingPointer(backend.cred, other))
        self.assertEqual(report.edges[0x1000], {("cred", 0x2000, "session_keyring")})
        self.assertEqual(collector.slab_creds, {0x2000})
        self.assertEqual(collector.known_creds, {0x2000})

    def test_memory_backed_file_and_credential_are_pinned_before_filter_and_path(self):
        tool = load_tool()
        for moving_file in (False, True):
            with self.subTest(moving_file=moving_file):
                backend = FakeBackend()
                backend.file.f_count = count(1)
                other_cred = Obj(0x2100, usage=count(9), session_keyring=0,
                                 process_keyring=0x1000, thread_keyring=0, request_key_auth=0)
                original_cred = backend.cred
                backend.file.f_cred = MovingPointer(original_cred, other_cred)
                other_file = Obj(0x3100, f_cred=other_cred, f_count=count(5), f_path="wrong")
                file = MovingPointer(backend.file, other_file) if moving_file else backend.file
                def path(file):
                    # Even a fresh access of f_cred after path lookup is wrong.
                    file.f_cred = other_cred
                    return file.f_path
                backend.path = path
                report = tool.Report([0x123])
                report.add_key(0x123, 0x1000, "key", 5, 1)
                collector = tool.Collector(backend, report, lambda _: None)
                collector.file(file, {"kind": "open_file"})
                self.assertEqual(report.edges[0x1000], {("cred", 0x2000, "session_keyring")})
                row = next(iter(report.holders[0x1000].values()))
                self.assertEqual((row["file_address"], row["file_count"], row["path"]),
                                 ("0x3000", 1, "a\nb"))

    def test_restriction_pointer_and_target_are_read_once(self):
        tool = load_tool()
        for moving_restriction in (False, True):
            with self.subTest(moving_restriction=moving_restriction):
                backend = FakeBackend()
                restriction = Obj(0x7000, key=MovingPointer(Obj(0x1000), Obj(0x1100)))
                backend.key.restrict_link = (MovingPointer(restriction, Obj(0x7100, key=0x1100))
                                             if moving_restriction else restriction)
                report = tool.Report([0x123, 0x124])
                report.add_key(0x123, 0x1000, "first", 5, 1)
                report.add_key(0x124, 0x1100, "later", 5, 1)
                tool.Collector(backend, report, lambda _: None).parent(backend.key)
                self.assertEqual(report.edges[0x1000], {("restriction", 0x1000)})
                self.assertEqual(report.edges[0x1100], set())

    def test_discovery_keeps_identity_and_holders_when_metadata_faults(self):
        tool = load_tool()
        class Fault:
            def __getattr__(self, name):
                raise RuntimeError("metadata fault")
        for fields in (("description",), ("uid",), ("usage",), ("description", "uid", "usage")):
            with self.subTest(fields=fields):
                backend = FakeBackend()
                backend.is_keyring = lambda key: False
                for field in fields:
                    setattr(backend.key, field, Fault())
                report = tool.Report([0x123])
                tool.collect(backend, tool.parse_args(["123"]), report, lambda _: None)
                data = report.as_dict()
                self.assertEqual(len(data["keys"]), 1)
                key = data["keys"][0]
                self.assertEqual((key["serial"], key["address"]), ("00000123", "0x1000"))
                for field, expected in (("description", "key"), ("uid", 5), ("usage", 3)):
                    self.assertEqual(key[field], None if field in fields else expected)
                self.assertEqual(data["missing_serials"], [])
                self.assertEqual(key["structural_refs"], 1)
                self.assertEqual(report.exit_code, 3)
                self.assertEqual(len(report.warnings), len(fields))
                for field in fields:
                    self.assertTrue(any(field in warning for warning in report.warnings))
                text = tool.render(report)
                self.assertIn("unavailable", text)
                if "usage" in fields:
                    self.assertNotIn("Counts differ", text)

    def test_discovery_pins_key_pointer_before_identity_and_metadata(self):
        tool = load_tool()
        backend = FakeBackend()
        other = Obj(0x1100, serial=0x124, description=string("wrong"), uid=Obj(val=99),
                    usage=count(99), type=0x99, keys=Obj(root=0), restrict_link=0)
        backend.keys = lambda: [MovingPointer(backend.key, other)]
        report = tool.Report([0x123])
        tool.collect(backend, tool.parse_args(["123"]), report, lambda _: None)
        key = report.as_dict()["keys"][0]
        self.assertEqual((key["serial"], key["address"], key["description"], key["uid"], key["usage"]),
                         ("00000123", "0x1000", "key", 5, 3))
        self.assertEqual(key["structural_refs"], 1)

    def test_incomplete_serial_tree_does_not_claim_absence(self):
        tool = load_tool()
        backend = FakeBackend()
        def keys():
            yield backend.key
            raise RuntimeError("tree fault")
        backend.keys = keys
        report = tool.Report([0x123, 0x124])
        tool.collect(backend, tool.parse_args(["123", "124"]), report, lambda _: None)
        self.assertEqual(report.as_dict()["missing_serials"], ["00000124"])
        text = tool.render(report)
        self.assertIn("NOT RESOLVED: 00000124", text)
        self.assertNotIn("NOT IN SERIAL TREE", text)
        complete = tool.Report([0x124])
        self.assertIn("NOT IN SERIAL TREE: 00000124", tool.render(complete))

    def test_unrelated_files_do_not_resolve_paths_or_warn(self):
        tool = load_tool()
        backend = FakeBackend()
        backend.cred.session_keyring = 0x9999
        def bad_path(file):
            self.fail("path lookup must not run for unrelated file credentials")
        backend.path = bad_path
        report = tool.Report([0x123])
        report.add_key(0x123, 0x1000, "key", 5, 1)
        collector = tool.Collector(backend, report, lambda _: None)
        collector.file(backend.file, {"kind": "allocated_file"})
        self.assertEqual(report.warnings, [])
        self.assertEqual(report.holders[0x1000], {})

    def test_tasks_maps_and_deferred_files_share_one_cred_edge(self):
        tool = load_tool()
        backend = FakeBackend()
        backend.nodes = {0x5000: Obj(slots=[0x6003, 0]),
                         0x6000: Obj(next_node=0x1002)}
        backend.key.keys.root = 0x5001
        backend.key.restrict_link = Obj(0x7000, key=0x1000)
        report = tool.Report([0x123])
        tool.collect(backend, tool.parse_args(["--deep", "123"]), report, lambda _: None)
        key = report.as_dict()["keys"][0]
        self.assertEqual(key["structural_refs"], 3)
        processes = [row for row in key["holders"] if row["kind"] == "process"]
        self.assertEqual(len(processes), 1)
        kinds = {row["kind"] for row in key["holders"]}
        self.assertTrue({"process", "open_file", "mapped_file", "allocated_file", "keyring", "restriction"} <= kinds)
        allocated = next(row for row in key["holders"] if row["kind"] == "allocated_file")
        self.assertEqual(allocated["state"], "deferred candidate")
        self.assertFalse(allocated["owning"])
        self.assertEqual(report.exit_code, 0)
        backend.nodes[0x6000].next_node = 0x5001
        report = tool.Report([0x123])
        tool.collect(backend, tool.parse_args(["123"]), report, lambda _: None)
        self.assertEqual(report.exit_code, 3)
        self.assertEqual(report.as_dict()["keys"][0]["structural_refs"], 1)

    def test_unknown_credential_is_not_leak_and_fault_preserves_evidence(self):
        tool = load_tool()
        backend = FakeBackend()
        backend.tasks = lambda: []
        report = tool.Report([0x123])
        tool.collect(backend, tool.parse_args(["--slab", "123"]), report, lambda _: None)
        self.assertEqual(report.as_dict()["keys"][0]["holders"][0]["kind"], "unknown_credential")
        self.assertEqual(report.exit_code, 0)
        self.assertIn("not prove a leak", tool.render(report))
        def faulty():
            yield backend.task
            raise RuntimeError("fault")
        backend.tasks = faulty
        report = tool.Report([0x123])
        tool.collect(backend, tool.parse_args(["123"]), report, lambda _: None)
        self.assertEqual(report.exit_code, 3)
        self.assertEqual(report.as_dict()["keys"][0]["structural_refs"], 1)


class ReverseTests(unittest.TestCase):
    def test_exhausted_budgets_do_not_advance_allocation_iterator(self):
        tool = load_tool()
        for option, value, expected in (("--max-objects", "1", 1), ("--max-bytes", "16", 1),
                                        ("--max-bytes", "15", 0), ("--max-hits", "1", 1),
                                        ("--timeout", "1", 1)):
            with self.subTest(option=option, value=value):
                backend = FakeBackend()
                cache = Obj(0x9000, name=string("cache"), object_size=16)
                backend.caches = lambda names: [cache]
                advances = []
                now = [0]
                def objects(cache):
                    advances.append(0x8000)
                    yield Obj(0x8000)
                    advances.append(0x8010)
                    raise RuntimeError("must not traverse past budget")
                backend.objects = objects
                def read(address, size):
                    if option == "--timeout":
                        now[0] = 2
                    return (0x1000).to_bytes(8, "little") * 2
                backend.read = read
                report = tool.Report([0x123])
                tool.scan_reverse(backend, tool.parse_args(["--reverse", option, value, "123"]),
                                  report, {0x1000: "key"}, lambda _: None, clock=lambda: now[0])
                self.assertEqual(len(advances), expected)
                self.assertEqual(report.reverse["objects"], expected)
                self.assertEqual(report.reverse["bytes"], expected * 16)
                self.assertEqual(report.exit_code, 3)
                self.assertEqual(len(report.warnings), 1)
                self.assertIn("reverse truncated: " + option[2:], report.warnings[0])

    def test_aligned_endian_payload_only_deduplicated_candidates(self):
        tool = load_tool()
        for width, order in ((4, "little"), (8, "little"), (8, "big")):
            backend = FakeBackend()
            backend.pointer_size, backend.byteorder = width, order
            cache = Obj(0x9000, name=string("kmalloc-256"), object_size=width * 2 + 1)
            backend.caches = lambda names: [cache, cache]
            backend.objects = lambda cache: [Obj(0x8000), Obj(0x8000)]
            payload = (0x1000).to_bytes(width, order) + (0x2000).to_bytes(width, order) + b"x"
            reads = []
            def read(address, size):
                reads.append((address, size))
                return payload
            backend.read = read
            report = tool.Report([0x123])
            tool.scan_reverse(backend, tool.parse_args(["--reverse", "123"]), report,
                              {0x1000: "key", 0x2000: "cred"}, lambda _: None)
            self.assertEqual(reads, [(0x8000, len(payload))])
            hits = report.reverse["candidates"]
            self.assertEqual([row["offset"] for row in hits], [0, width])
            self.assertEqual(hits[1]["target_kind"], "cred")
            self.assertFalse(hits[0]["owning"])
            self.assertEqual(report.exit_code, 0)
            self.assertEqual(list(tool.pointer_matches(b"x" + payload, 0x8001, width, order, {0x1000: "key"})), [])

    def test_budgets_faults_and_time_are_partial(self):
        tool = load_tool()
        backend = FakeBackend()
        cache = Obj(0x9000, name=string("cache"), object_size=16)
        backend.caches = lambda names: [cache]
        backend.objects = lambda cache: [Obj(0x8000), Obj(0x8010)]
        backend.read = lambda address, size: (0x1000).to_bytes(8, "little") * 2
        for option, value in (("--max-hits", "1"), ("--max-objects", "1"), ("--max-bytes", "15")):
            report = tool.Report([0x123])
            tool.scan_reverse(backend, tool.parse_args(["--reverse", option, value, "123"]),
                              report, {0x1000: "key"}, lambda _: None)
            self.assertEqual(report.exit_code, 3, option)
            self.assertLessEqual(report.reverse["bytes"], 32)
        report = tool.Report([0x123])
        clock = iter([0, 100, 100, 100])
        tool.scan_reverse(backend, tool.parse_args(["--reverse", "123"]), report,
                          {0x1000: "key"}, lambda _: None, clock=lambda: next(clock))
        self.assertEqual(report.exit_code, 3)
        def fault(address, size):
            raise RuntimeError("unreadable allocation")
        backend.read = fault
        report = tool.Report([0x123])
        tool.scan_reverse(backend, tool.parse_args(["--reverse", "123"]), report,
                          {0x1000: "key"}, lambda _: None)
        self.assertEqual(report.exit_code, 3)
        self.assertEqual(report.reverse["candidates"], [])


class RuntimeTests(unittest.TestCase):
    def test_vmas_pin_task_mm_before_truth_test_and_iteration(self):
        import types
        from unittest.mock import patch
        tool = load_tool()
        modules = {name: types.ModuleType(name) for name in (
            "drgn", "drgn.helpers", "drgn.helpers.linux", "drgn.helpers.linux.mm")}
        calls = []
        def vmas(mm):
            # The helper itself accesses its argument more than once.
            calls.append((int(mm), int(mm)))
            return []
        modules["drgn.helpers.linux.mm"].for_each_vma = vmas
        for first in (0, 0x5000):
            with self.subTest(first=first), patch.dict(sys.modules, modules):
                calls.clear()
                backend = FakeBackend()
                backend.task.mm = MovingPointer(Obj(first), Obj(0x6000))
                backend.vmas = lambda task: tool.DrgnBackend.vmas(backend, task)
                report = tool.Report([0x123])
                report.add_key(0x123, 0x1000, "key", 5, 1)
                tool.Collector(backend, report, lambda _: None).task(backend.task, True)
                self.assertEqual(calls, [(first, first)] if first else [])
                self.assertEqual(report.warnings, [])

    def test_json_stdout_file_and_interrupt_preserve_partial(self):
        import contextlib
        import io
        import json
        import tempfile
        from unittest.mock import patch
        tool = load_tool()
        for interrupted in (False, True):
            backend = FakeBackend()
            if interrupted:
                def tasks():
                    yield backend.task
                    raise KeyboardInterrupt
                backend.tasks = tasks
            stdout, stderr = io.StringIO(), io.StringIO()
            with tempfile.TemporaryDirectory() as directory:
                path = str(Path(directory) / "report.json")
                with patch.object(tool, "DrgnBackend", return_value=backend), contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
                    status = tool.main(["--json", "--output", path, "123"], program=object())
                self.assertEqual(status, 3 if interrupted else 0)
                self.assertEqual(Path(path).read_text(), stdout.getvalue())
            report = json.loads(stdout.getvalue())
            self.assertEqual(report["status"], "partial" if interrupted else "complete")
            self.assertIn("Scanning", stderr.getvalue())
            self.assertNotIn("\x1b", stderr.getvalue())
            self.assertEqual(report["keys"][0]["structural_refs"], 1)

    def test_drgn_facade_allocated_char_api_and_platform(self):
        import types
        from unittest.mock import patch
        tool = load_tool()
        cache = Obj(123)
        calls = []
        modules = {name: types.ModuleType(name) for name in (
            "drgn", "drgn.helpers", "drgn.helpers.linux", "drgn.helpers.linux.slab")}
        modules["drgn"].PlatformFlags = Obj(IS_LITTLE_ENDIAN=1)
        modules["drgn.helpers.linux.slab"].find_slab_cache = lambda prog, name: cache if name == "known" else None
        modules["drgn.helpers.linux.slab"].for_each_slab_cache = lambda prog: [cache]
        def allocated(cache_arg, type_name):
            calls.append((cache_arg, type_name))
            return [Obj(0x1000)]
        modules["drgn.helpers.linux.slab"].slab_cache_for_each_allocated_object = allocated
        program = Obj(platform=Obj(flags=1), type=lambda name: Obj(size=8))
        with patch.dict(sys.modules, modules):
            backend = tool.DrgnBackend(program)
            self.assertEqual(backend.byteorder, "little")
            self.assertEqual(backend.pointer_size, 8)
            self.assertEqual(list(backend.caches(["known"])), [cache])
            self.assertEqual(int(list(backend.objects(cache))[0]), 0x1000)
            self.assertEqual(calls, [(cache, "char")])
            with self.assertRaises(RuntimeError):
                list(backend.caches(["absent"]))


if __name__ == "__main__":
    unittest.main()
