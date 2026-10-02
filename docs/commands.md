# Command guide

## Common conventions

The seven Bash commands use **0** for success/help, **1** for operational failure,
and **2** for invalid usage. Results go to stdout, diagnostics to stderr. `-h` and
`--help` display usage without connecting to a server or cluster. These conventions
do not describe the unchanged Python `ocptool`.

Runtime baseline: Linux, Bash 4.4+, GNU coreutils/findutils and the command-specific
dependencies below. Development checks additionally require Python 3.9+, Make and
the packages in `requirements-dev.txt`. OpenSSL 1.1.1+ and jq 1.6+ are the intended
minimums; CI exercises the distributions shipped with Ubuntu 24.04, not every
historical dependency version. Use supported vendor versions for production.

Keep the checkout layout intact; `ced` and `certinfo` source `lib/tls.sh`. No
installation into system directories is necessary. Examples below assume the
repository root as the working directory.

## `certinfo`: certificate details

```bash
./linux/certinfo example.com
./linux/certinfo --timeout 5 example.com:8443
./linux/certinfo --servername example.com '[2001:db8::1]:443'
```

Usage: `certinfo [--timeout SECONDS] [--servername NAME] HOST[:PORT]`.
The default port is 443, timeout 10 seconds. The timeout is a positive integer up
to 999999; GNU `timeout` allows one additional second before forced termination.
IPv6 must be bracketed. Scoped zone identifiers are not supported. DNS targets send
SNI automatically; IP targets do not unless `--servername` explicitly supplies it.

Prints host, port, CN, issuer, expiration, and DNS/IP subject alternative names.
Missing CN or DNS/IP SANs are shown as `(none)`; this is not a parsing failure.
IPv6 SANs retain the complete address; OpenSSL may expand and capitalise their
representation. Other SAN types (such as URI/email) are not included. Subject
values use OpenSSL escaping rather than unsafe shell interpretation.

Dependencies: OpenSSL, GNU `timeout`, `mktemp`, `dirname`, `cat`, `rm`, `sed`, `tr`.
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
disables colors. `tput` is optional. Dependencies are the TLS helper's core tools,
OpenSSL and GNU `date`; no trust or hostname validation is performed.

## `gkc`: validate Kustomize builds

```bash
# Run from anywhere inside the Git working tree to inspect the whole tree.
/path/to/toolbox/linux/gkc
```

No operational arguments. Requires Git, GNU `find`/`sort`, and `oc` with the
`kustomize` subcommand. Finds `kustomization.yaml`, `kustomization.yml`, and
`Kustomization`, excluding `.git`; each distinct directory is built once.
Whitespace/newlines in paths are preserved. Untracked files and directories are
included; Git ignore rules are not used to restrict discovery.

Uses `oc kustomize` as the source of validation. Unrelated documentation or helper
files need not appear in a kustomization. Continues through build failures and
returns 1 if any fail. No matches is an explicit successful empty result.

Does not apply resources or need cluster access for local resources. Remote
Kustomize bases can trigger downloads and need network access/authentication.
Inspect untrusted repositories before building them. CLI/Kustomize version changes
can affect validation; use the same `oc` version as your operational workflow.

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

## `ocptool`: unchanged, separate work

```bash
python3 openshift/ocptool --help
python3 openshift/ocptool capacity
python3 openshift/ocptool nstop -n application
python3 openshift/ocptool ptop --limit 20
python3 openshift/ocptool free
```

Requires Python 3.8+, `oc` with appropriate cluster access, and **`tabulate`** (its
current import is mandatory even for help). Commands query nodes/pods, descriptions
and metrics; access depends on the selected action. Review the script before use.

This tool is deliberately unchanged. Known follow-up concerns include decimal
memory conversions, default-namespace metrics matching, missing metrics represented
as zero, the unused `--label` option and error handling. The `free` report's aggregate
N-1 arithmetic is not a scheduler guarantee. Do not rely on these reports for
capacity or upgrade decisions without independent validation. The new Bash test
suite does not validate its accounting; only Python syntax is checked.
