# mcm — managed multi-cluster login and shell execution

[Command guide](commands.md) · [Executable](../openshift/mcm)

Requires POSIX (tested on Linux), Python 3.9+, PyYAML, `oc`, and the shell commands
being executed. The command is independently copyable. Help and registration
operations are local; no cluster or credential access is required.

```bash
./openshift/mcm add staging https://api.staging.example.com:6443 ~/.kube/mcm-staging \
  --credential-group corporate --user alice --certificate-authority ~/certs/corporate-ca.pem
./openshift/mcm add production https://api.production.example.com:6443 ~/.kube/mcm-production \
  --credential-group corporate
./openshift/mcm list
./openshift/mcm login --cluster staging
./openshift/mcm exec --cluster staging --command 'oc get nodes' --workers 4 --output table
./openshift/mcm --config ./registrations.yaml exec --command 'oc get projects' --timeout 60
./openshift/mcm logout --cluster staging
./openshift/mcm remove staging
```

`exec` logs in automatically when needed: a separate `login` is optional, not a
prerequisite. **Without repeatable `--cluster NAME`, login/exec/logout select every
registration.** An unknown selection or no selected targets is an error.

## Registration and migration

Default registration file: `~/.mcm.yaml`; global `--config FILE` must precede the
subcommand. Relative kubeconfig and CA paths are resolved against that file's
directory, **not the invocation directory**. `~` is expanded; environment-variable
substitution inside YAML is not supported. Saves normalize paths to absolute paths.

```yaml
credential_groups:
  corporate: alice
  separate-provider: alice
clusters:
  - name: staging
    server: https://api.staging.example.com:6443
    kubeconfig: .kube/mcm-staging
    credential_group: corporate
    certificate_authority: certs/corporate-ca.pem  # optional existing PEM file
  - name: production
    server: https://api.production.example.com:6443
    kubeconfig: .kube/mcm-production
    credential_group: corporate
```

A group maps explicitly to one username (`corporate: {username: alice}` is also
accepted). Only group membership shares an in-memory password; matching usernames
alone never imply matching credentials. The registered username must equal the
actual `oc whoami` identity exactly. Identity-provider aliases are not guessed.

- `add NAME SERVER KUBECONFIG --credential-group GROUP [--user USER]` creates a
  registration; a new group requires `--user`. An existing group's username cannot
  be silently changed. `--certificate-authority FILE` sets persistent CA trust.
- The old `add ... --user USER` syntax still works and creates an isolated group
  named `legacy:NAME` for that cluster.
- Existing entries with `username` instead of `credential_group` are loaded as
  isolated `legacy:NAME` groups. Identical old usernames **do not share passwords**.
  To migrate deliberately, define a shared group and replace each entry's
  `username` with `credential_group`. Do not set both. `add`/`remove` writes the
  normalized group schema; read-only `list` does not rewrite registrations.
- `list` prints sorted names, servers, usernames and group names.
- `remove NAME` requires an existing name. It removes only the registration, not
  the kubeconfig, group definition, or server session. Log out first if desired.

Schema errors, duplicate names, invalid HTTPS origins and missing groups fail
before cluster requests. Every registration must have a dedicated writable
kubeconfig. Empty paths, colon-separated KUBECONFIG lists, shared paths, symlinks
(including parent components), hard-linked files, non-regular files, registration
or lock-file paths, and `~/.kube/config` are rejected. CA files cannot also be writable
registration/kubeconfig targets. Keep the directory private and trusted; these
checks are not protection against a hostile process running as the same user.

Registration saves use a stable sidecar `.lock` across read-modify-write, followed
by a same-directory atomic replacement. New registration, lock and kubeconfig
files are mode 0600; newly created directories are 0700. Existing parent directory
permissions are not changed. Concurrent registration additions do not lose updates.
Do not run simultaneous login/logout operations against the same target: token
revocation and authentication are not a distributed transaction.

## Authentication and preflight

For each selected target, mcm builds a private temporary kubeconfig containing
only the selected server/context/token and optional namespace/CA. It ignores the
ambient `KUBECONFIG`; execution uses that isolated snapshot. Cached server mismatch
fails closed **before contacting it**; `oc whoami` then validates actual identity.
Managed kubeconfigs accept token authentication only: exec/auth-provider plugins,
client keys and embedded passwords are rejected rather than run or copied.
A missing kubeconfig is initialized automatically. A malformed file is not erased.

Valid sessions are reused without prompting. Explicit unauthenticated responses
trigger managed `oc login`; network, missing executable, TLS and other errors are
not interpreted as requests for a password. Authentication runs sequentially to
avoid prompt collisions. The human prompt occurs only after `oc` requests
`Password:`. A group needing authentication is prompted once per invocation.

Passwords are kept only in process memory and sent through a private PTY, whose
echo is disabled **before** starting `oc`. They are never placed in argv,
environment variables or files. The PTY is necessary because the tested real
`oc` 4.18 non-TTY password scanner truncates whitespace. Leading/trailing spaces
are preserved; terminal control characters are rejected. An interactive terminal
with echo control is required; mcm refuses getpass's unsafe echoed-input fallback.
It does not implement browser MFA, alternate interactive challenges or automatic
credential retries. Auth child output is suppressed because it could contain
credentials; errors give a safe category rather than replaying server responses.

A failed attempt after supplying credentials blocks further attempts in that
group for the invocation, including uncertain network failures after submission.
Already-valid targets in the group can still pass preflight. Password rejection
is not retried on another target. Successful login is revalidated before its token
is persisted; incorrect identity/server is never accepted as success.

**Default: authenticate/validate all selected targets, then execute none if any
preflight fails.** Earlier successful logins remain; they are not rolled back.
`--continue-on-error` explicitly allows commands on healthy targets, but the final
exit status remains nonzero. Once command execution starts, a command failure
does not cancel other commands. No mutating command is retried automatically.

## TLS

TLS certificate and hostname verification are enabled by default. Configure
`certificate_authority`/`--certificate-authority` for private roots; otherwise
system trust or a cached CA is used. There is no silent fallback after a TLS error.

For a deliberate one-off exception:

```bash
./openshift/mcm exec --cluster staging --insecure-skip-tls-verify --command 'oc get nodes'
```

This warns on stderr with **all affected selected target names** and applies to
preflight, login and execution for this invocation only. It disables endpoint
verification and can expose credentials to an impersonating server; prefer a CA.
An old kubeconfig's insecure setting is removed, never silently honored. Temporary
insecure login state is not persisted: token saves strip the bypass. The next
invocation verifies TLS again. Custom CA settings remain persistent.

## Execution, output and failures

Commands run locally through `/bin/sh`, once per healthy target, with its isolated
`KUBECONFIG`. They are not automatically prefixed with `oc`, nor are they remote
shell sessions. **This is trusted shell input, not a sandbox**: an explicit
`--server`, `--kubeconfig`, environment reassignment, or arbitrary shell code can
bypass mcm's chosen target. Quote commands and never interpolate untrusted input.
There is no confirmation, dry-run, rollback or read-only restriction. Local file
operations repeat on the same host and may race. Use only the RBAC permissions
needed for the requested commands, not a blanket cluster-admin grant.

`--workers N` is positive, default 8, and bounds parallel execution/logout workers.
Preflight/authentication is sequential. Results are buffered and sorted by cluster
name. `--output raw` (default) labels status and child exit code; `table` prints a
compact status row followed by the **complete** stdout. Both modes retain complete
stderr separately with cluster labels; neither hides stderr behind nonempty stdout.
Output is intended for humans, not as a stable machine-readable API.

`--timeout SECONDS` is positive and finite, default 30, per child process (not a
whole-invocation budget). It also bounds time spent entering a password; increase
it if needed. Every mcm-owned `oc` preflight/login/logout invocation receives
`--request-timeout` in addition to the subprocess deadline. Arbitrary shell
commands are bounded at the subprocess level; mcm does not rewrite their `oc`
arguments. On timeout, SIGINT or SIGTERM, mcm kills/reaps owned process groups,
including ordinary shell descendants. Deliberately detached processes that leave
the group are outside this guarantee. Timed-out operations may already have
changed server state; inspect before retrying.

`logout` validates target/identity first, never logs in just to log out, and checks
`oc logout` success before clearing the persistent token and reporting `logged out`.
On failure the local token is retained; server-side effects may be uncertain.
Removing a registration is not equivalent to logging out.

Exit codes: **0** all selected operations successful/help; **1** any configuration,
authentication, validation, timeout or command failure; **2** invalid CLI usage;
**130** interruption/cancellation. The child command's code is displayed, while
mcm returns an aggregate 1. Empty `list` succeeds; empty login/exec/logout fails.

## Offline validation

`make check` includes syntax/lint and fake-oc tests in isolated homes, including
real private PTYs, process cancellation and concurrent config writes. No real
cluster or credentials are used. Optional compatibility tests use a locally
verified `oc` binary against a loopback TLS/OAuth fixture:

```bash
MCM_REAL_OC=/absolute/path/to/oc .venv/bin/python -m unittest discover \
  -s tests -p test_mcm_real_oc.py -v
```

Those tests never download a binary and are skipped unless `MCM_REAL_OC` is set.
The fixture covers the CLI password flow, not every OpenShift identity provider.
