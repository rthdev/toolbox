# ogn — node capacity by role and topology

```sh
ogn
ogn --help
```

`ogn` is a self-contained Bash script requiring `oc`, `jq`, and `awk` on
`PATH`. Use an authenticated OpenShift context with permission to list nodes.
The report makes one read-only `oc get nodes -o json` call. It accepts no
filters or positional arguments; `-h` and `--help` work without dependencies
or cluster access. Unexpected arguments are rejected locally.

## Report

Columns are `NAME`, `ROLES`, `CPU`, `MEMORY`, `REGION`, and `ZONE`.
Widths expand to include the header and every node. CPU and memory are
right-aligned; the other columns are left-aligned.

CPU and memory are the **raw `status.capacity` strings**, including their
original units. They are not usage, allocatable capacity, or converted
quantities. Missing capacity and topology fields display `N/A`.

Recognized role labels use the `node-role.kubernetes.io/` prefix:

- `master` and `control-plane` both display as `master`, once even when both
  labels are present.
- `infra` and `worker` retain their names.
- Multiple recognized roles display comma-separated in master, infra, worker
  order. Nodes with no recognized role keep a blank role field.

Sort precedence is **master/control-plane first, infra second, worker last**.
A multi-role node uses its highest-priority role. Within each group, sorting
is by `topology.kubernetes.io/region`, then `topology.kubernetes.io/zone`,
then node name as a deterministic tiebreaker. Nodes without recognized roles
retain the existing fallback group alongside workers. Missing topology sorts
as the displayed `N/A` string.

## Failures and empty results

Exit status is **0** for a successful report/help and **2** for invalid arguments.
Missing dependencies return **127**; `oc` and `jq` failures preserve their nonzero
status rather than mapping every operational failure to 1.

Collection and JSON processing complete before any report is printed.
An `oc` or `jq` failure returns nonzero, preserves diagnostics on stderr,
and emits no partial table or misleading header on stdout. Invalid node-list
structure or non-string report fields are errors. Control characters and
internal field separators in report fields are rejected rather than breaking
column layout. A valid empty `items` array succeeds with just the header.

## Offline verification

```sh
python3 -m unittest discover -s tests -p test_ogn.py -v
make check
```

The ogn tests isolate `PATH`, provide an `oc` stub, and use real Bash, jq, and
awk. They never contact a cluster. They cover failures, malformed snapshots,
help and dependency checks, mixed-role precedence, deterministic topology
sorting, raw capacity units, dynamic alignment, missing fields, and empty
results. `make check` also includes Bash syntax and ShellCheck for ogn.
