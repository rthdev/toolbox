# `keyrefs`: read-only kernel key-reference evidence

Copy `linux/keyrefs` alone; it has no repository dependencies. Python 3.9+ is
required. Actual collection runs inside drgn with matching kernel debug information
and permission to read the live kernel (normally root) or the selected dump.
The drgn boundary uses the 0.2.0 allocated-slab APIs; unsupported layouts/helpers
produce partial warnings rather than guessed offsets. Help needs only Python.

```bash
python3 linux/keyrefs --help
sudo drgn -k ./linux/keyrefs 01234567 07654321
sudo drgn -k ./linux/keyrefs --deep --verbose 01234567
drgn -c /path/vmcore -s /path/vmlinux ./linux/keyrefs --deep 01234567
sudo drgn -k ./linux/keyrefs --reverse --cache kmalloc-256 --cache filp \
  --max-bytes 16777216 --max-objects 100000 --max-hits 200 --timeout 30 \
  --json --output /root/keyrefs-report.json 01234567
```

## Options and scope

`keyrefs [--maps] [--slab] [--files] [--deep] [--reverse]
[--cache NAME ...] [--max-bytes BYTES] [--max-objects N] [--max-hits N]
[--timeout SECONDS] [--verbose] [--json] [--output FILE] HEX_SERIAL...`

Serials are **hexadecimal**, just as in `/proc/keys`, even when all characters are
digits. Optional `0x` is accepted. One to eight hexadecimal digits must represent
1 through `7fffffff`; malformed values are rejected, not repaired. Duplicates are
collapsed. Resolution uses `key_serial_tree` globally, without a task/UID filter.

Default scans:

- Every task thread's `cred` and `real_cred` fields (identical observations collapse).
- Every task's open FDs, including file credentials and `d_path()` paths.
- Incoming keyring assoc-array links, handling node/shortcut/keyring-leaf tag bits,
  and keyring restriction keys. Parent serials identify useful follow-up targets.

Additional scans:

| Option | Meaning |
| --- | --- |
| `--maps` | File credentials for task VMAs, including mappings whose FD was closed |
| `--slab` | Allocated `cred_jar` objects, including credentials without a named task/file holder |
| `--files` | Allocated `filp` objects; zero/nonpositive file counts are marked **deferred candidate** |
| `--deep` | Enable maps, slab and files; does **not** enable reverse |
| `--reverse` | Enable slab, then search allocated object payloads for target key and matching slab-credential pointers |
| `--cache NAME` | Restrict reverse to named caches; repeatable, requires `--reverse` |
| `--verbose` | Show addresses for structural holder evidence |
| `--json` | Machine report instead of human text |
| `--output FILE` | Also save exactly the stdout report using exclusive create, mode 0600 |

PIDs/TIDs are from the initial PID namespace; kernel mount-tree paths can differ
from container/chroot paths. Shared file tables and mappings can appear under
multiple tasks. Structural edges count once per `(credential address, key field)`,
not once per observing task/file. Parent links and restrictions are separate edges.
Credential fields scanned are `session_keyring`, `process_keyring`, `thread_keyring`
and `request_key_auth`. Nonpositive credential counts and deferred file observations
are not counted as proven structural references through those observations.

## Bounded allocated-slab reverse search

The tool enumerates allocations using
`slab_cache_for_each_allocated_object(cache, 'char')`. It reads exactly
`cache.object_size` bytes, not allocator padding/slot size. Only pointer-sized words
aligned in the **target address space** are compared, using target pointer size and
endianness. Repeated cache/allocation addresses are deduplicated. Freed objects are
excluded by the allocated-object iterator; a live kernel can change after that
observation. No raw memory buffers or secret payload bytes are printed.

All caches are selected by default. Conservative reverse defaults:

- `--max-bytes 67108864`: 64 MiB of attempted allocation payload reads.
- `--max-objects 250000`: unique attempted allocations.
- `--max-hits 1000`: pointer matches.
- `--timeout 60`: seconds, measured from the start of the reverse stage.

All budget values are positive decimal integers. These limits apply **only to
reverse**, not preceding task/keyring/maps/typed-slab stages. Reaching a limit,
a cache-enumeration failure, a skipped cache or a memory-read fault marks the report
partial and returns 3. Exact limit equality is conservatively partial. A cache
filter is an explicitly selected scope; unselected caches are not errors.

Timeout checks are cooperative, between caches and allocations; drgn can spend
longer than the deadline inside a helper or read. This is not a hard process time
limit. Stages and periodic object counts go to stderr with no ANSI. Ctrl-C during
collection renders evidence gathered so far as partial, where Python can deliver
the interrupt. SIGKILL cannot preserve a report. Other scan stages can also be slow.

A reverse hit reports the cache name, allocation start and payload size, matching
word address and offset, and target kind/address. It is an **allocated pointer
candidate**, not proof of an owning reference, and is never added to structural
reference counts. Generic `kmalloc-*` cache names cannot identify the C type or
member. There are no arbitrary struct casts or claims of automatic owning-type
recovery. `--inspect` is not implemented.

This is **not** equivalent to crash `search -k`: stacks, globals, vmalloc and freed
allocations are outside this reverse search. It cannot discover a reference count
increment whose last pointer was lost. Unaligned/tagged/encoded pointers are not
reverse matches. No holder found, unexplained count differences, or an UNKNOWN
HOLDER credential do **not** establish a leak. Pointer slots used repeatedly are
read into value pointers before address/field accesses, avoiding attribution of
one pointed-to object's fields to another address. This does not pin object
lifetimes or freeze their fields; live reads are not a consistent snapshot.

## Reports, safety and exit status

Human output groups structural holders by serial, includes PID/FD/path where
available, and compares distinct observed structural edges with kernel key usage.
These are positive observations, not an exhaustive reference-count audit.
Descriptions, paths and diagnostics are escaped to prevent terminal control text.
Reverse candidate rows always include addresses because those are their diagnostic
identity. JSON always includes addresses regardless of `--verbose`.

`--output` creates a new file, refuses existing files and final-component symlinks,
and requests mode 0600 (a restrictive umask may reduce permissions further). It
does not create directories. Choose a trusted existing parent directory. Stdout is
still emitted if saving fails; output failure returns 1. Reports contain process
names, paths, key descriptions, UIDs and kernel addresses: protect them accordingly.
The command never calls keyctl or changes kernel/keyring state.

- **0:** Selected scans completed; unknown holders or absent serials are valid results.
- **1:** Fatal setup/output error (including running collection under ordinary Python).
- **2:** Invalid arguments.
- **3:** Partial collection, read/layout/helper errors, interrupted or bounded scan.

On a partial scan, missing serials are printed as **NOT RESOLVED**, not categorically
absent. In particular, a partial serial-tree traversal only establishes that a
serial was not resolved in the observed portion. A discovered serial/address is
retained even if its description, UID or usage is unreadable: those fields show
`<unavailable>` in human output, with partial warnings, and holder scans continue.
Unknown usage is not compared numerically with the observed reference count.

## Live-kernel validation

The consolidated tool was exercised on Rocky Linux 9.8 x86_64, running
`5.14.0-687.17.1.el9_8.x86_64`, with matching debug information and drgn 0.2.0
under Python 3.9.25. Real fixtures covered task-held revoked/expired sessions,
open-file credentials, mmap-held files after FD closure, an incoming keyring link,
and a file retained only by a Unix-socket SCM_RIGHTS message queue.

A separate disposable-VM-only fixture module held a credential in a real allocated
`kmalloc-256` object. The earlier scans found the credential but no task/file owner.
The reverse scan found the exact allocation and credential pointer at offset
`0x40`, matching independently read fixture metadata. The raw match remained a
candidate and did not inflate structural-reference counts. This fixture module
is not required by the diagnostic and is not loaded by it.

Live checks also exercised byte/object/hit limits, an unavailable cache, JSON and
human output, mode-0600 saved reports and refusal to overwrite an existing report.
A live test exposed unnecessary path resolution for unrelated files; the tool now
checks the credential/key match before reading the path, with an offline regression.

This is not validation of a RHEL binary, every vendor backport, or every subsystem
that can retain credentials. Vmcore execution and live restriction/deferred-zero
reference branches have not been exercised. Endianness, pointer alignment, injected
read failures and interruption are covered by offline fixtures, not alternate-CPU
live-kernel tests.

## JSON schema version 1

Top-level fields:

- `schema_version`: integer `1`.
- `status`: `complete` or `partial` (selected scans only).
- `scope`: explanatory string, not an exhaustive audit claim.
- `keys`: serial-sorted array; each entry has `serial` (eight lowercase hex digits),
  `address` (`0x` hex string), `description`, `uid`, `usage`, `structural_refs`, `holders`.
  `description` is a string or `null`; `uid` and `usage` are integers or `null`.
  `null` means metadata could not be collected, not an absent key or zero count;
  the report is partial and warnings identify unavailable fields (or interruption).
- `missing_serials`: requested serials not resolved, eight-digit hex strings.
- `warnings`: strings describing incomplete work.
- `reverse`: `enabled`, `bytes`, `objects`, `candidates`; when enabled also
  `cache_filter` and `limits` (`bytes`, `objects`, `hits`, `seconds`).

Each holder has `kind`, `edge` (structural identity array) and `owning` (whether this
observation contributes an edge). `kind` is `process`, `open_file`, `mapped_file`,
`allocated_file`, `unknown_credential`, `keyring`, or `restriction`. Credential
holders include `cred_address`, `cred_usage`, `field`. Task observations add `pid`,
`tid`, `comm`; FDs add `fd`; file observations add `file_address`, `file_count`,
`path`, `state`. Parent observations use `parent_serial`, `parent_address`,
`description`. Unknown credentials have `state: "UNKNOWN HOLDER"`.
`edge` contains `["cred", integer_address, field]`, `["link", integer_address]`,
or `["restriction", integer_address]`; addresses elsewhere are hex strings.

Each reverse candidate contains `cache`, `allocation` (hex), `size` (bytes),
`match_address` (hex), `offset` (bytes), `target_kind` (`key` or `cred`),
`target_address` (hex), and `owning: false`. No candidate payload bytes are included.
Consumers should tolerate new fields and check both exit status and `status`.
