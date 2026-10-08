# Toolbox

Small command-line helpers for Linux administration, troubleshooting and OpenShift.
Commands intentionally have no `.sh` or `.py` extension.
Copyable teaching templates live separately in `templates/`.

## Commands

| Command | Purpose | Main runtime dependencies |
| --- | --- | --- |
| [`ced`](linux/ced) | Quick TLS certificate expiration summary | Bash, OpenSSL, GNU coreutils |
| [`certinfo`](linux/certinfo) | TLS certificate CN, issuer, expiration and SANs | Bash, OpenSSL, GNU coreutils |
| [`findav`](linux/findav) | Find files starting with an Ansible Vault header | Bash, GNU findutils/coreutils |
| [`gencl`](linux/gencl) | Print a tag-grouped Git changelog | Python 3, Git |
| [`qrm`](linux/qrm) | List Quay repositories/tags or delete a tag with confirmation | Python 3.9+, Requests, Quay HTTPS API |
| [`mcm`](openshift/mcm) | Store cluster entries and run shell commands across them | Python 3.9+, PyYAML, `oc`, shell |
| [`ogn`](openshift/ogn) | List node capacity, roles, region and zone | Bash, `oc`, jq, awk |
| [`lsswap`](linux/lsswap) | Top processes by swap usage in MiB | Bash, Linux `/proc`, awk, coreutils |
| [`pls`](linux/pls) | Container owner/name hints from visible conmon processes | Bash, procps `ps`, text utilities |
| [`kdf`](openshift/kdf) | Disk-free report for all PVCs and running pod mounts in a namespace | Bash, `kubectl`, jq, container `df` |
| [`ocprems`](openshift/ocprems) | API-removal request counts and recent callers | Bash, `oc`, jq |
| [`ocmt`](openshift/ocmt) | OpenShift Capacity Management Tool: node/pod resources and N-1 headroom | Python 3.9+ (standard library), `oc` |

See the [command guide](docs/commands.md) for options, examples, dependencies,
permissions, exit codes and limitations.

## Demo template

[`templates/demo-template.sh`](templates/demo-template.sh) is a self-contained Bash
workflow demonstration template. Copy it, edit its header and `demo()` sequence,
then press Enter to execute each displayed command. Failures do not stop the demo.
See the [editing guide](docs/demo-template.md) for pipelines, redirection and quoting.

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
templates/             Copyable workflow demonstration templates
docs/commands.md       Command reference and operational limits
tests/                 Offline regression tests and loopback TLS integration
.github/workflows/     Pull-request validation
Makefile               Local syntax, lint and test entry points
requirements-dev.txt   Pinned development tools
CONTRIBUTING.md        Development and branch/PR workflow
```

## Operational safety

- Inspect scripts and test in a non-production environment before operational use.
- Verify the target host, cluster, identity and namespace. Read-only cluster
  reporters use existing CLI credentials. [`mcm`](docs/mcm.md) can log in and
  modify its configured kubeconfigs; shell commands run against **every registered
  cluster**, without confirmation or dry-run. Its automatic login disables TLS
  verification and passes the password as a process argument.
- [`qrm`](docs/qrm.md) tag deletion requires interactive confirmation or `--yes`.
  Preview the exact target with its network-free `--dry-run` before deleting.
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
