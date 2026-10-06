# Command guide

## Common conventions

Use **0** for success/help, **1** for operational failure, and **2** for invalid
usage. Write results to stdout and diagnostics to stderr. `-h` and `--help`
should display usage without connecting to a server or cluster.

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
