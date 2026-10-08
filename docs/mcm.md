# mcm — multi-cluster shell execution

[Command guide](commands.md) · [Executable](../openshift/mcm)

```bash
./openshift/mcm --help
./openshift/mcm add staging https://api.staging.example.com:6443 "$HOME/.kube/staging" --user alice
./openshift/mcm list
./openshift/mcm exec --command 'oc get nodes' --workers 4 --output table
./openshift/mcm remove staging
```

Requires Python 3.9+, PyYAML (`yaml` import, including for help), `oc` for cluster
operations, and a shell/the commands being executed. Stores registrations in `~/.mcm.yaml`; no
config-path override is available. Subcommands:

- `add NAME SERVER KUBECONFIG --user USER`: adds an entry and rewrites the YAML
  file; duplicate names raise an error. Does not contact or validate the cluster.
- `list`: prints registered names, servers and usernames (not kubeconfig paths).
- `remove NAME`: rewrites the YAML without that name; an absent name is not an
  error. Does not delete the kubeconfig or log out.
- `logout`: runs `oc logout` sequentially for **every** registration, using each
  configured kubeconfig. This can invalidate sessions and modifies login state.
- `exec --command SHELL_COMMAND [--workers N] [--output raw|table]`: executes on
  **every** registration; no cluster selector or exclusion option. Defaults are
  eight workers and `raw`. Workers must be positive for execution to work, but
  argparse only checks that the value is an integer.

Top-level and subcommand `-h/--help` are local and return 0 before reading config.
Invoking `mcm` without a subcommand loads config and then displays help. A missing
config means an empty list; an `exec` against it can succeed without running
anything. YAML entries must have `name`, `server`, `kubeconfig` and `username`
fields. Paths are stored verbatim:
use absolute kubeconfig paths and let your invoking shell expand `$HOME`; literal
`~` inside a stored path is not expanded by this script.

Before execution, `oc whoami` checks run in parallel. Any failure triggers sequential
login, with one password prompt per username and an in-memory password cache for
that process. **Automatic login passes the password as an `oc -p` command-line
argument and always sets `--insecure-skip-tls-verify=true`.** This exposes credentials
to applicable process observers and disables server certificate verification;
use only controlled, trusted environments. Existing successful `whoami` is accepted
without checking it matches the registered server or username. Inspect each
kubeconfig/context yourself; registration is not proof of the actual target.

Commands run through `shell=True` locally, with that entry's `KUBECONFIG` environment
variable. They are not automatically prefixed with `oc` and are not remote shell
sessions. Quote the command as one argument; never interpolate untrusted input.
**There is no confirmation, dry-run, rollback or read-only restriction.** Destructive
`oc` commands affect all registered targets, while local file operations repeat on
the same machine and may race. Login can rewrite configured kubeconfigs; shared
kubeconfig paths can also cause interference. The registration file is overwritten
without locking, backup or explicit restrictive permissions. Protect config and
kubeconfig files yourself; permissions/RBAC must cover the exact requested commands,
not a blanket cluster-admin grant.

Execution is parallel, but results are buffered and printed sorted by cluster name.
Each shell command has a fixed 30-second subprocess timeout, with no CLI override;
login, login checks and logout have no such timeout, and shell descendants are not
guaranteed to stop on timeout. `raw` prints stdout if nonempty, otherwise stderr,
without a status field. `table` adds OK/ERROR but shows only the first line of the
same selected stream; neither displays both streams when stdout is present.

Help/normal completion returns 0 and argparse usage errors return 2. **Failed or
timed-out per-cluster commands still leave the overall exit code 0.** Logout ignores
`oc` return codes and can print `logged out` on failure. Uncaught config, login,
duplicate-name or invalid-worker errors normally return 1. A login failure stops
execution before the command phase; earlier successful logins are not undone.
