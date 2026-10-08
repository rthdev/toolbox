# qrm — Quay repository management

`linux/qrm` is a standalone Python 3.9+ script requiring `requests`. Install
`requests` into the Python environment used to run it; the repository's
`requirements-dev.txt` includes tested versions for the CI Python versions.
Direct execution uses `/usr/bin/python3`; invoke `python3 linux/qrm` to select
your Python environment. `python3 linux/qrm --help` exits 0 without network
access, but Requests must be installed even for help or dry-run.
No cluster client or other toolbox command is required.

## Read-only actions

```sh
python3 linux/qrm -r registry.example
python3 linux/qrm -r registry.example -a listrepos
python3 linux/qrm -r registry.example -a listtags -p team/image --silent
```

- `-r/--registry` is a DNS hostname or IPv4 address, optionally `:port` (1–65535).
  Supply no scheme, credentials, path, query, or fragment. Connections use HTTPS
  with normal certificate verification. IPv6 literals are not supported.
- `-a/--action` retains its existing choices: `printalltags` (default), `listtags`,
  `listrepos`, and `deltag`.
- The default action prints a Repository/Tags table, with comma-separated tags.
- `listrepos` prints a JSON array aggregating all repository pages;
  `listtags` prints Quay's tag-name-to-metadata JSON object.
- Repository enumeration retains `public=true`; providing a token does not
  change that filter or promise an inventory of all private repositories.
- `-p/--repo` requires exactly `namespace/name` for `listtags` and `deltag`.
  Empty components, dot segments, whitespace and control characters are rejected.

## Authentication

Reads can be anonymous where the server allows it. Set `QRM_TOKEN` through your
secret manager or environment, or supply a token file:

```sh
python3 linux/qrm -r registry.example -a listrepos --token-file "$HOME/.config/quay/token"
```

`--token-file` overrides `QRM_TOKEN`. The file contains only the bearer token;
its surrounding whitespace is stripped. Protect it with permissions such as
`0600`. Explicitly empty or malformed credentials are rejected before networking.
The token value is never accepted as a command-line option or printed in
checks/errors. `.netrc` credentials are not used. HTTP redirects are rejected,
including for read-only requests, rather than forwarding credentials or mutations.
Tokens must have the permissions required by the Quay action (write permission
for deleting a tag).

## Deletion safeguards

Preview a deletion without credentials, prompting, or **any network requests**:

```sh
python3 linux/qrm -r registry.example -a deltag -p team/image -t old --dry-run
```

The JSON preview contains `dry_run`, `method`, and the fully encoded target URL.
It does not verify server existence, permissions, or tag existence.

```sh
# Interactive: type yes at the stderr prompt to authorise this exact target.
python3 linux/qrm -r registry.example -a deltag -p team/image -t old --token-file "$HOME/.config/quay/token"

# Automation: explicitly authorise deletion.
python3 linux/qrm -r registry.example -a deltag -p team/image -t old --yes --token-file "$HOME/.config/quay/token"
```

Without `--yes`, noninteractive deletion fails before networking. An interactive
response other than `yes` (including EOF) cancels. `--silent` never bypasses the
confirmation or suppresses errors. `--yes` and `--dry-run` are accepted only for
`deltag`; when combined, dry-run wins. `-t/--tag` is required for deletion.
Namespace, repository name and tag are encoded separately as URL path components;
encoding is not a promise that Quay accepts arbitrary tag or repository names.

Deletion uses `DELETE /api/v1/repository/{namespace}/{repository}/tag/{tag}`.
This is the `deleteFullTag` operation in the [official Quay API documentation](https://docs.quay.io/api/swagger/),
confirmed against the [Quay tag endpoint implementation](https://github.com/quay/quay/blob/master/endpoints/api/tag.py)
(the route at `RepositoryTag` and its `delete` method, returning an empty HTTP 204).
An empty successful response prints JSON `null`; a nonempty successful JSON
response is preserved. No automatic retry is made: after a timeout the server
may already have completed deletion, so inspect its state before retrying.

## Failure and output contract

- Successful actions, dry-run and help exit 0.
- Arguments are validated before network access. Usage/cancellation errors exit 2;
  HTTP, connectivity, malformed JSON, and schema failures exit 1.
- Each request has a 5-second connection timeout and 30-second read timeout.
  These are Requests timeouts, not an overall command deadline; DNS and a server
  trickling response bytes can take longer. Requests are not retried.
- The discovery check must succeed before the action runs. A separate socket
  probe is no longer used. TLS, DNS, connection and timeout failures stop work.
- Absent/null/empty pagination cursors terminate successfully. Repeated cursors
  and more than 1,000 pages fail, without printing partial results.
- HTTP failures are checked before JSON decoding, including empty error bodies.
  Repository and tag response shapes are checked before rendering.
- Checks/prompts/errors go to stderr; JSON actions leave stdout machine-readable.
  `-s/--silent` suppresses routine checks, not errors or action output.

**Intentional safety changes:** deletion now requires confirmation or `--yes`,
redirects are refused, malformed arguments/responses fail closed, pagination is
bounded, and connectivity failures abort instead of allowing the action to run.
Existing action flags, the default table action, and the public repository filter
remain unchanged.

## Offline verification

`tests/test_qrm.py` mocks HTTP and blocks unexpected Requests transport calls.
It never contacts or mutates a registry. `make check` covers syntax, lint and the
full repository regression suite; other tests may use loopback TLS fixtures.
