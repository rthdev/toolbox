# Command guide

## Common conventions

The standard convention is **0** for success/help, **1** for operational failure,
and **2** for invalid usage, with results on stdout and diagnostics on stderr.
Check each command's guide for exceptions: `ogn` preserves external-command
failure statuses, and `mcm` returns 1 if any selected target fails (130 on
cancellation). The commands documented here support local `-h`/`--help` without
server or cluster access; Python imports still require installed runtime packages.

Runtime baseline: Linux, Bash 4.4+, GNU coreutils/findutils and the command-specific
dependencies below. Development checks additionally require Python 3.9+, Make and
the packages in `requirements-dev.txt`. OpenSSL 1.1.1+ and jq 1.6+ are the intended
minimums; CI exercises the distributions shipped with Ubuntu 24.04, not every
historical dependency version. Use supported vendor versions for production.

The Bash commands are self-contained and can be copied individually; no repository
layout or shared library is required. Install their external runtime dependencies.
Examples below assume the repository root as the working directory.

## `certinfo`: certificate details

```bash
./linux/certinfo example.com
./linux/certinfo --timeout 5 example.com:8443
./linux/certinfo --servername example.com 192.0.2.7:443
```

Usage: `certinfo [--timeout SECONDS] [--servername NAME] HOST[:PORT]`.
The default port is 443, timeout 10 seconds. The timeout is a positive integer up
to 999999; GNU `timeout` allows one additional second before forced termination.
Targets are domain names or IPv4 addresses; connections use IPv4. IPv6 is not
supported yet. DNS targets send SNI automatically; IP targets do not unless
`--servername` explicitly supplies it.

Prints host, port, CN, issuer, expiration, and DNS/IP subject alternative names.
Missing CN or DNS/IP SANs are shown as `(none)`; this is not a parsing failure.
Other SAN types (such as URI/email) are not included. Subject values use OpenSSL
escaping rather than unsafe shell interpretation.

Dependencies: OpenSSL, GNU `timeout`, `mktemp`, `cat`, `rm`, `sed`, `tr`.
Connection or certificate parsing failures return 1 without a partial result table.
Temporary transport files are removed when the command finishes.

**Inspection is not verification.** The tool intentionally displays self-signed,
expired and hostname-mismatched certificates. Exit 0 means inspection succeeded,
not that the certificate chain or endpoint identity is trusted. It inspects the
first certificate presented, not a full chain report, and does not support STARTTLS.

## `ced`: quick expiration check

```bash
./linux/ced example.com
NO_COLOR=1 ./linux/ced --timeout 5 example.com:8443
```

Shares the target, timeout and SNI options above. Prints `EXPIRES:` and
`DAYS REMAINING:`. Uses GNU `date`; the former hard-coded `datediff` dependency is
removed. Day counts are whole remaining 24-hour periods rounded down, so even a
recently expired certificate has a negative count. Expiration itself does not
change the success exit code: this is a report, not a monitoring-threshold API.

On suitable terminals, less than 30 days is red, less than 90 yellow, otherwise
green. Redirected output, `TERM=dumb`, an unset/empty `TERM`, or any set `NO_COLOR`
disables colors. `tput` is optional. Dependencies are OpenSSL, GNU `timeout`,
`mktemp`, `cat`, `rm` and GNU `date`; no trust or hostname validation is performed.

## `findav`: locate Ansible Vault files

```bash
./linux/findav                         # current directory, not recursive
./linux/findav -r -0 ./roles            # recursive, NUL-terminated paths
```

Read-only first-line Vault header detection; no Ansible installation or password
is needed. Requires Bash, GNU findutils and coreutils. See the [findav guide](findav.md)
for path/symlink handling, output delimiters and incomplete-scan failures.

## `gencl`: changelog from Git tags

```bash
./linux/gencl                          # run within the target Git repository
./linux/gencl 'Next release'           # heading only; does not create a tag
```

Requires Python 3 and Git. Generates Markdown from the caller's local repository,
without modifying tags or commits. See the [gencl guide](gencl.md) for tag ordering,
revision ranges, argument validation and failure handling.

## `keyrefs`: kernel key-reference evidence

```bash
python3 linux/keyrefs --help                 # no drgn import needed
sudo drgn -k ./linux/keyrefs --deep 01234567
drgn -c vmcore -s vmlinux ./linux/keyrefs --reverse --cache kmalloc-256 01234567
```

Self-contained, read-only Python 3.9+ drgn diagnostic. Resolves hexadecimal serials
in the global kernel serial tree, then reports task/file credential references and
incoming keyring links. Optional allocated-slab reverse scanning finds pointer
candidates, **not proven owning references**. Requires matching debug information
and kernel-memory access (normally root for live kernels). Exit **3** means partial
or bounded/truncated evidence; unknown holders alone do not imply failure or leaks.
See the [keyrefs guide](keyrefs.md) for budgets, JSON, secure saved reports and limits.

## `qrm`: Quay repository and tag requests

```bash
python3 linux/qrm --help
python3 linux/qrm -r quay.example.com -a listtags -p team/application
python3 linux/qrm -r quay.example.com -a deltag -p team/application -t old --dry-run
```

Requires Python 3.9+ and Requests. Use `python3 linux/qrm` to select your Python
environment; direct execution uses `/usr/bin/python3`. Read actions use HTTPS;
deletion requires confirmation or `--yes`, while `--dry-run` is network-free.
See the [qrm guide](qrm.md) for authentication, permissions, pagination, timeouts
and output/exit-status contracts.

## `mcm`: multi-cluster shell execution

```bash
./openshift/mcm --help
./openshift/mcm list
```

Requires Python 3.9+ and PyYAML, plus `oc` and the shell commands used for cluster
operations on POSIX. Registrations default to `~/.mcm.yaml` (`--config FILE` overrides).
`login` and `exec` automatically authenticate as needed, prompting once per explicit
credential group through private terminal input. TLS verification is enabled by
default, with custom CA support and a warned, invocation-only insecure override.
**Without repeatable `--cluster NAME`, operations select every registration.** All
selected targets must pass server/identity preflight before commands run, unless
`--continue-on-error` is explicit; any target failure returns 1. Both output streams
are retained, and subprocesses have configurable timeouts with process-group cleanup.
Read the [mcm guide](mcm.md) for configuration migration and operational limits.

## `ogn`: node roles and topology

```bash
./openshift/ogn --help
./openshift/ogn
```

Requires Bash, an authenticated `oc`, jq and awk, with permission to list nodes.
Reports raw node capacity, recognized roles, region and zone without modifying
resources. See the [ogn guide](ogn.md) for role precedence, topology sorting,
alignment and failure/empty-result semantics.

## `lsswap`: process swap use

```bash
./linux/lsswap
./linux/lsswap --limit 20
```

Requires Linux `/proc`, Bash, `sort` and `awk`. Default limit is 10; `--limit N`
accepts a positive integer. Reads each accessible process status once, sorts by
swap usage descending (PID breaks ties), and prints `SWAP(MiB)`, PID and process
name. Names containing spaces are preserved. Zero-swap processes can appear;
processes without a usable swap field are omitted.

Disappearing, unreadable or incomplete process statuses are skipped quietly. This
is a best-effort snapshot, not an atomic system total. Permissions/procfs mount
options can limit visibility; no privilege escalation is attempted. `PROC_ROOT`
can point at an alternate mounted proc filesystem or a test fixture. An invalid
root is an operational error. No available records produces the header only.

## `pls`: visible conmon containers

```bash
./linux/pls
```

Requires Bash, procps-compatible `ps`, and `sort`. Prints sorted `owner:name`
records from visible processes whose command and executable basename are exactly
`conmon`; recognises `-n`, `--name`, and `--name=`. No visible named conmon processes
is successful empty output. A failed process query returns 1.

This is a process-based hint, **not a complete Podman inventory**: stopped
containers, inaccessible processes and runtimes without conmon are absent.
`ps` flattens argument boundaries, so a name containing option-like text is
ambiguous; ordinary embedded spaces are preserved. Container names are never
executed or evaluated. Owner names are subject to the process tool's output format.

## `kdf`: namespace PVC filesystem usage

```bash
./openshift/kdf                            # current-context namespace
./openshift/kdf -n application             # selected namespace
./openshift/kdf --namespace application
```

Usage: `kdf [-n NAMESPACE|--namespace NAMESPACE]`. Requires Bash, `kubectl` and jq.
Lists **all PVCs** in the specified namespace or the current kubeconfig context's
namespace; it never scans all namespaces or switches context. There are no PVC,
pod or container selectors.

Prints one whitespace-aligned table with these eight columns, in normal `df` style:

```text
PVC Name  Pod Name  Filesystem  Size  Used  Avail  Use%  Mounted on
```

Size, Used, Avail and Use% are right-aligned; the other columns are left-aligned.
Column widths include the header and all successful or blank rows. The table is
buffered until all mounts have been inspected, while diagnostics are written
immediately to stderr. Use% is copied from `df`, not recalculated from rounded
human-readable sizes. Spaces and literal pipes in filesystem and mount paths
are preserved; pipes are not column separators.

Fetches PVCs and pods once each, matches actual PVC-backed volume names to regular
container mounts, and reports every matching **Running pod** and mount path.
Rows are sorted by PVC, pod and mount path. The same pod/path shared by containers
is inspected once. When container statuses are available, only running containers
are eligible; absent/null container statuses fall back to regular containers in a
Running pod. Pending, completed and other non-Running pods are never exec targets.
Init and ephemeral containers are excluded.

A PVC with no eligible running filesystem mount gets one row with only its PVC
name and all other fields blank. This includes unmounted claims and raw-block
PVCs, which cannot be inspected with `df`. No PVCs produces just the header.

Runs `kubectl ... exec POD -c CONTAINER -- df -P -h -- PATH` per unique pod/path.
The container must have `df` supporting `-P`, `-h` and `--`. Paths are passed as
quoted arguments, including spaces. Numeric columns are parsed from portable
`df` output; mountpoint spaces are preserved and repeated headers are omitted.
The report describes the filesystem visible at the mount, not the requested PVC
allocation or an independent storage-backend usage measurement. Different mounts
can report the same underlying filesystem; values must not be summed as PVC totals.

This executes a read-only reporting command inside workloads; no helper pods or
resources are created. Required namespace permissions: list PVCs, list/get pods,
and create `pods/exec`; some CLI transports additionally require get on `pods/exec`.
Use narrowly scoped RBAC rather than cluster-admin.

Query or malformed discovery-data failures return 1. Exec/df failures or unparseable
`df` output write diagnostics to stderr, omit usage for that failed mount, and
continue with the remaining mounts, returning 1 overall. No usage is fabricated.
Successful reports and help return 0; invalid arguments return 2. Discovery is a
snapshot: workloads may change before exec, and a Running pod does not guarantee
exec access or the presence of `df`.

## `ocprems`: APIs marked for removal

```bash
./openshift/ocprems
```

No operational arguments. Requires `oc`, jq and cluster-scoped list access to
`apirequestcounts.apiserver.openshift.io`. Reads one APIRequestCount snapshot,
selecting entries with `status.removedInRelease`, sorted by API name. Prints the
reported removal release, `status.requestCount`, and distinct username/verb/user-agent
tuples from `status.last24h[].byNode[].byUser[]`.

Caller rows span the API server's retained `last24h` buckets. The displayed request
count is the resource's reported total, not a per-caller count calculated by this
tool; it is not recomputed from the displayed tuples. Exact duplicate tuples across
buckets/nodes are collapsed, but different verbs or user agents are retained.
OpenShift sampling/retention and API-server restart behaviour govern completeness:
this is not a full audit log or proof that an upgrade is safe. Check current
OpenShift upgrade guidance separately.

Missing values display `unknown` or `(no caller data)`; no removal entries is an
explicit successful empty result. Tabs, newlines and backslashes within caller
fields are TSV-escaped. Output may contain usernames and client identifiers; treat
it as operational information when sharing. No cluster resources are changed.

## `ocmt`: OpenShift Capacity Management Tool

```bash
./openshift/ocmt                              # capacity (the default)
./openshift/ocmt capacity --node-role worker
./openshift/ocmt nstop                        # current-context namespace
./openshift/ocmt nstop -n application --sort-by MEM_USAGE
./openshift/ocmt ptop --limit 30 --sort-by CPU_REQUEST
./openshift/ocmt ptop -n application --label 'topology.kubernetes.io/zone=east'
./openshift/ocmt free --node-role worker --timeout 15
```

Standalone successor to `ocptool`, retaining its four report modes and option
names. Requires Python 3.9+ and a configured, logged-in `oc`; no `tabulate`, jq,
or other Python package is required. It never changes contexts or cluster resources.

### Modes and options

- **`capacity`** (default): allocatable, effective requests, declared limits and
  metrics usage per node, with totals grouped by node role. Includes all matching
  nodes, displaying Ready/NotReady and cordoned state. Role priority is control
  (`control-plane` or `master`), infra, then worker; unlabelled nodes retain the
  worker fallback.
- **`nstop`**: all pods in `-n/--namespace` or the current kubeconfig namespace
  (defaults to `default` if unset). Includes unassigned and completed pods, and a
  total for all matching rows. Unassigned nodes display `N/A`.
- **`ptop`**: top 20 assigned pods across all namespaces, or just `-n/--namespace`
  when supplied. `--limit N` selects a positive number of rows. Unassigned pods are
  excluded, as in the original; use `nstop` to inspect them. No total is shown for
  this truncated ranking.
- **`free`**: per-role N-1 aggregate headroom. From the total allocatable of Ready,
  uncordoned matching nodes, subtract the largest node allocation independently
  for CPU and memory, then subtract all assigned nonterminal pod requests on
  matching nodes, including NotReady/cordoned nodes. Reports the worst CPU/memory
  node names (which can differ). A single available node leaves zero effective
  N-1 capacity; negative free values are retained. No available nodes shows `none`
  for the worst-node names. All matching roles share one table, one header and
  common column widths; no matching nodes produces an explicit message instead.

`--node-role {control,infra,worker}` and `--label SELECTOR` apply to nodes in every
mode; pod reports then include only pods assigned to those nodes. `--label` is
now effective, passed as a single node-selector argument to `oc`; Kubernetes
validates selector syntax. It is **not** a pod-label selector. Combining the
options intersects their filters.

`--sort-by COLUMN` works in pod reports: `POD`, `NODE`, `CPU_REQUEST`, `MEM_REQUEST`,
`CPU_LIMIT`, `MEM_LIMIT`, `CPU_USAGE`, `MEM_USAGE`, and additionally `NAMESPACE` for
`ptop`. Sorts descending (including text), defaulting to `CPU_REQUEST`; missing
values sort last, and namespace/pod name break ties deterministically.

`--timeout SECONDS` is an integer from 1 to 3600, default 30. Each `oc` invocation
has both a server request timeout and a subprocess wall-clock timeout. The latter
bounds the entire invocation, including discovery/pagination. There are no retries.
Namespace must be a valid lowercase DNS label. Options irrelevant to a mode are
rejected instead of silently ignored: namespace/sort are pod-report options and
limit is ptop-only. Help does not require `oc` or cluster access.

### Accounting and interpretation

CPU is reported in cores, memory in MiB, both to two decimal places. Kubernetes
binary, decimal, milli/micro/nano and exponent quantities are resource-aware:
`1048576` bytes is 1 MiB, `1G` is about 953.67 MiB, and `250m` CPU is 0.25 cores.
Memory `m` means millibytes, not MiB. Totals use unrounded values, so displayed
rounded rows need not sum exactly to displayed totals.

Requests/limits come from **pod specifications**, not human-readable `describe`
output. Application container sums are compared with ordered init-container
peaks; restartable init sidecars accumulate during subsequent initialization and
continue alongside application containers. Pod-level resource budgets, where
present, override that resource's container aggregate. Pod overhead is added to
requests and to nonzero limits. Ephemeral containers do not add reservations.
Missing requests/limits contribute zero. **Declared limit totals are not workload
ceilings**: an unspecified limit means unbounded, not a zero cap, and partially
limited workloads can exceed the displayed sum.

Node accounting excludes unassigned and Succeeded/Failed pods, but includes
assigned Pending pods and nonterminal terminating pods. Pod reports retain all
phases in their scope, so their totals can differ from node reservations. In-place
resize status/allocated-resource reconciliation is not implemented: during a
resize, these spec-based values may differ from the scheduler's current accounting.

Usage is read from structured `metrics.k8s.io/v1beta1` snapshots, keyed by actual
namespace and pod name. In pod reports (`nstop` and `ptop`), Succeeded/Failed pods
have known zero current CPU/memory usage, even if stale metrics remain; their
spec-based requests/limits are unchanged. For nonterminal pods and node samples,
missing CPU/memory, absent samples, and missing application or restartable-sidecar
container samples display **`unknown`**, never zero. Any unknown constituent makes
that usage total unknown; terminal pods contribute zero, so an all-terminal
`nstop` report has zero usage totals. Available extra container samples are included
in nonterminal pod usage. Metrics endpoint failures (including RBAC denial, timeout
or invalid list JSON) emit a warning but still produce a successful resource report
with unknown usage except for terminal pods. Invalid resource quantities or
malformed resource records fail rather than fabricate capacity.

**N-1 is arithmetic, not a scheduling or failover guarantee.** It does not model
resource fragmentation, taints/tolerations, affinity/topology, pod-count limits,
volumes, extended resources, quotas, pending demand or disruption budgets. Even
Ready uncordoned nodes may be ineligible for a workload. `capacity` totals are an
inventory, including unavailable nodes; only `free` excludes those allocations.
Snapshots and metrics windows are not atomic and may disagree during changes.

Capacity values at or above 60% of allocatable are red only on a suitable TTY.
Redirected output, `TERM=dumb`, an unset/empty `TERM`, or any set `NO_COLOR` disables
color. Tables are plain aligned Markdown with no terminal-library dependency.
CPU_/MEM_ columns, including headers and totals, are right-aligned; identifiers
are left-aligned. Unknown usage and other text in resource columns retain that
same alignment.

### Collection, permissions and failures

`capacity` uses three bulk `oc` calls (nodes, all pods, node metrics); `free` uses
two (nodes, all pods). Pod reports use two (pods and pod metrics), plus one node
list if filtering by node, and one local kubeconfig lookup for implicit `nstop`
namespace. Command count is independent of pod/node count; `oc` may make additional
API discovery or pagination requests internally. JSON travels through stdout,
never through command-line payload arguments. Snapshots are held in memory.

Grant list access to nodes and cluster-wide pods for node reports; pod reports
need pod list access only in their scope, plus node list access when filtering by
node. Usage additionally needs list access to `nodes.metrics.k8s.io` or
`pods.metrics.k8s.io` in the relevant scope. No exec, writes or cluster-admin grant
is required. Core-data failures return **1**, invalid CLI usage **2**, and successful
reports/help **0** (including degraded metrics with warnings). Core collection and
calculation complete before report output; diagnostics go to stderr.

Compared with `ocptool`, intentional changes are corrected resource accounting,
namespace-aware metrics, effective label filtering, explicit missing usage,
bounded/error-checked collection, mode-specific argument validation, TTY-safe
color, and conservative N-1 wording/available-node handling. `ptop -n` now scopes
the report rather than ignoring the namespace. These are reports, not a stable
machine-readable output API. The original executable is unchanged.
