# Toolbox

Small command-line helpers for Linux administration, troubleshooting and OpenShift.
Commands intentionally have no `.sh` or `.py` extension.

## Commands

| Command | Purpose | Main runtime dependencies |
| --- | --- | --- |
| [`ced`](linux/ced) | Quick TLS certificate expiration summary | Bash, OpenSSL, GNU coreutils |
| [`certinfo`](linux/certinfo) | TLS certificate CN, issuer, expiration and SANs | Bash, OpenSSL, GNU coreutils |
| [`lsswap`](linux/lsswap) | Top processes by swap usage in MiB | Bash, Linux `/proc`, awk, coreutils |
| [`pls`](linux/pls) | Container owner/name hints from visible conmon processes | Bash, procps `ps`, text utilities |
| [`kdf`](openshift/kdf) | Disk-free report for all PVCs and running pod mounts in a namespace | Bash, `kubectl`, jq, container `df` |
| [`ocprems`](openshift/ocprems) | API-removal request counts and recent callers | Bash, `oc`, jq |
| [`ocmt`](openshift/ocmt) | OpenShift Capacity Management Tool: node/pod resources and N-1 headroom | Python 3.9+ (standard library), `oc` |

See the [command guide](docs/commands.md) for options, examples, dependencies,
permissions, exit codes and limitations.

## Getting started

```bash
git clone https://github.com/rthdev/toolbox.git
cd toolbox
./linux/certinfo --help
./linux/lsswap
```

The Bash commands are self-contained: copy an individual command to your preferred
location once its external dependencies are installed. No sibling scripts or shared
libraries are required. `ocmt` is also independently copyable and needs no Python
packages. Run the executable directly or with its interpreter:

```bash
bash linux/certinfo example.com:443
```

This command makes a TLS connection. Install runtime dependencies appropriate to
the commands you use; there is no repository-wide runtime package installation.

## Layout

```text
linux/                 Linux and certificate commands
openshift/             Cluster commands
docs/commands.md       Command reference and operational limits
tests/                 Offline regression tests and loopback TLS integration
.github/workflows/     Pull-request validation
Makefile               Local syntax, lint and test entry points
requirements-dev.txt   Pinned development tools
CONTRIBUTING.md        Development and branch/PR workflow
```

## Operational safety

- Inspect scripts and test in a non-production environment before operational use.
- Verify the target host, cluster, identity and namespace. Cluster tools use your
  existing CLI credentials; they do not log in or switch contexts for you.
- `kdf` executes `df` inside a container. `ocprems` reads cluster-scoped API request
  data. Neither needs a blanket cluster-admin grant; see the permission notes.
- Certificate inspection is **not** certificate-chain or hostname verification.
  Successful output does not establish that an endpoint is trusted.
- Do not commit credentials, tokens, private keys or kubeconfig files.

Before cluster operations, for example:

```bash
oc config current-context
oc whoami --show-server
oc whoami
oc project
# For kdf, also confirm the context used by kubectl:
kubectl config current-context
```

## Development

See [CONTRIBUTING.md](CONTRIBUTING.md) for environment setup and `make check`.
Tests require no real cluster or internet connection, but local TLS integration
uses loopback sockets. Dependency installation and CI runner setup require network
access. Every change starts on a new branch and is submitted as a PR to `main`;
never write directly to `main`.

A licence has not yet been selected.
