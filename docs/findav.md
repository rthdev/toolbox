# findav

`linux/findav` lists regular files whose first line starts with the literal
`$ANSIBLE_VAULT;` prefix. It identifies whole-file Vault header candidates; it
does not validate encryption, decrypt content, parse YAML, or detect inline
`!vault` values. A header without a final newline is accepted.

## Usage

```text
findav [-r|--recursive] [-0|--null] [--] [START_PATH]
findav [-h|--help]
```

With no path, scan the current directory. Directory scans are nonrecursive by
default; `-r` includes descendants. An explicit regular file is checked directly.
Options precede the path. Use `--` for a path beginning with a dash:

```sh
findav
findav -r 'directory with spaces'
findav -- -private
findav -r -0 -- -private
```

For directory paths that `find` could interpret as expressions (a leading dash,
`!`, or `(`), output uses a `./` prefix. Other supplied path spellings are kept.
A directly supplied file path is printed as supplied.

Directory traversal does not follow symlinks, including an explicitly supplied
symlink to a directory. An explicitly supplied symlink to a regular file is
checked, preserving the original command's scope.

## Output and status

Default output is one matching path followed by a newline, without escaping.
Use `-0` / `--null` for NUL-terminated paths when filenames can contain newlines;
spaces, backslashes, and newlines in paths are preserved. Output is not sorted.
Diagnostics go to stderr, never into the path stream.

- **0:** scan completed, including when there are no matches; also successful help.
- **1:** invalid/inaccessible starting path, traversal failure, or unreadable,
  vanished, or read-failed file. Scan failures report that the scan is incomplete.
- **2:** unknown option or too many starting paths.

Scanning continues past individual file failures. Matches may already have been
printed before a later error: a nonzero status means stdout is only a partial
result, not a complete inventory. Check exit status even when stdout is nonempty.
Only the first line is inspected; the encrypted body is not read or verified.
As with ordinary filesystem scans, concurrent changes are not a snapshot.

## Requirements and local tests

Requires Bash, `find` supporting `-maxdepth` and `-print0`, and `head` supporting
`-n 1 --` (GNU/Linux utilities satisfy these). Help does not invoke external
commands. The executable is independently copyable.

```sh
python3 -m unittest discover -s tests -p test_findav.py -v
bash -n linux/findav
shellcheck linux/findav
```

Fixtures use temporary directories. Deterministic command stubs exercise traversal
and read failures even when run as root; an additional permission test runs only
when permissions can restrict the test user. No Ansible, network, or live secrets
are needed.
