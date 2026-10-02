# Contributing

Keep changes small and focused. Preserve existing executable names unless a migration
has been agreed. The repository intentionally contains extensionless commands.

## Branch and pull request workflow

Never commit or push directly to `main`, including through GitHub's file-editing API.
Start each task on a new branch from the current upstream main:

```bash
git status --short                  # preserve any existing work first
git fetch origin
git switch -c fix/descriptive-name origin/main
# add a regression test, observe its failure, then implement the fix
make check
git add <specific-files>
git commit -m "fix: describe the change"
git push -u origin HEAD
gh pr create --base main
```

Describe behaviour changes, compatibility effects, actual validation and limitations
in the PR. Do not claim live cluster validation from fixture tests. Leave merging to
the maintainer. Do not select or change the project's licence without agreement.

## Development environment

Linux, Bash 4.4+, Python 3.9+, GNU coreutils, GNU/procps tools, Git, OpenSSL 1.1.1+
and jq 1.6+ form the development baseline. CI runs on Ubuntu 24.04 with Python 3.9
and 3.14. Per-command runtime dependencies are listed in [the command guide](docs/commands.md).

With Python's standard venv/pip tools:

```bash
python3 -m venv .venv
. .venv/bin/activate
python -m pip install -r requirements-dev.txt
make check
```

Or with `uv`, including hosts whose system Python has no pip:

```bash
uv venv --python python3 .venv
uv pip install --python .venv/bin/python -r requirements-dev.txt
PATH="$PWD/.venv/bin:$PATH" make check PYTHON="$PWD/.venv/bin/python"
```

The development tools are version-pinned; no Python package is required to execute
the Bash commands themselves. Installing development dependencies requires network
access. The test suite does not require internet access, kubeconfig, cluster
credentials or a running container engine; its TLS integration tests bind loopback.
Tests must use isolated temporary directories and must not modify user configuration.

## Checks and boundaries

- `make syntax`: Bash parsing and a non-executing Python AST check of `ocptool`.
- `make lint`: ShellCheck on explicit extensionless Bash command paths/shared shell
  helpers, Ruff on the new Python tests only.
- `make test`: Python stdlib unittest discovery, including real local TLS fixtures.
- `make check`: all of the above. No blanket suppression of lint errors.

`openshift/ocptool` is deliberately excluded from the current hardening work and
from new Python lint rules. Its syntax check does **not** validate its resource
accounting or cluster behaviour; those need a separate change and test suite.

Test failure and argument validation as well as happy paths. Use stdout for results,
stderr for errors, and document dependencies, scope, permissions and exit codes.
Never commit credentials, tokens, private keys or kubeconfig files. Generate test
certificates at runtime and clean up their keys with the test's temporary directory.
