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

## `kdf`: mounted PVC filesystem usage

```bash
./openshift/kdf -n application data
./openshift/kdf -n application --pod worker-0 --container app data
./openshift/kdf -n application                 # all PVCs, sorted by name
```

Usage: `kdf [-n NAMESPACE|--namespace NAMESPACE] [-c CONTAINER|--container CONTAINER]
[--pod POD] [PVC]`. Supports `--` before the positional PVC name.
Requires `kubectl` and jq. Uses the specified namespace or the current kubeconfig
context's namespace; it never scans all namespaces or switches context.

Fetches PVCs once and pods once when needed, then matches PVC-backed volumes to
regular containers and their mount paths. Multiple eligible pods require `--pod`;
multiple containers require `--container`. It never arbitrarily selects the first
pod/container. All unique paths within the selected container are inspected,
including paths with spaces. Init/ephemeral containers and raw block volume devices
are not inspected. Pod readiness is not guaranteed by discovery; exec can still fail.

Runs `kubectl ... exec POD -c CONTAINER -- df -h -- PATH...`. The container must have
`df` supporting `-h` and `--`. This executes a read-only filesystem-reporting command
inside a workload; it does not create helper pods or change resources. The output
is filesystem usage visible at the mounts, not the requested PVC allocation or an
independent storage-backend usage measurement.

Required permissions in the namespace: list PVCs, list/get pods, and create
`pods/exec`; some CLI transports additionally require get on `pods/exec`. Even
single-PVC mode lists PVCs. Use narrowly scoped RBAC rather than cluster-admin.

No PVCs is a successful empty result. A named PVC not found, no matching mount,
ambiguous selection, malformed data, query failure or exec failure returns 1.
All-PVC mode stops at the first failure; earlier results may already be printed.
Discovery is a snapshot; pods can change between discovery and exec.

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
