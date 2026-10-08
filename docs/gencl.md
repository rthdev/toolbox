# gencl

Generate a Markdown changelog from the current local Git repository. Requires
Python 3 and Git on `PATH`; no Python packages or network access are needed.

```sh
gencl                 # first section is headed HEAD
gencl 'Next release'  # change only the first section heading
gencl -h
gencl --help
```

Run from the repository or one of its subdirectories. When running directly
from this toolbox checkout, use the path to `linux/gencl`.

The optional argument is a **heading**, not a revision, tag to create, or
history filter. Quote headings containing spaces or shell metacharacters.
Exactly zero or one heading is accepted; arguments beginning with `-` are
reserved for options. Only `-h` and `--help` are supported, each used alone.
Help and argument validation run without Git or a repository.

## Output semantics

- Start with `# Changelog`.
- Sort tags using Git's existing descending version order (`--sort=-v:refname`),
  not creation date or ancestry order.
- The first section contains commits in the highest-sorted tag's `..HEAD` range.
- Each tag section contains commits in the preceding lower-sorted tag's range
  up to that tag. The lowest-sorted tag's section contains its reachable history.
- With no tags, the first section contains the current HEAD's entire history.
- Commit lines retain Git's `--oneline --no-merges --no-decorate` format. Empty
  sections are retained. Annotated and lightweight tags are supported.

Tag names are passed to Git as fully qualified refs in argument arrays, never
through a shell. The report is collected before anything is written to stdout.
A failed Git command therefore produces an error on stderr, a nonzero exit
status, and no partial changelog. An empty repository with no commits is an
error, not a successful empty changelog.

Exit status: **0** for a completed report or help, **1** for Git execution/history
errors, **2** for invalid arguments. This command does not modify tags or commits.

To save a report, redirect stdout, for example `gencl 'Next release' > CHANGELOG.md`.
Shell redirection truncates an existing destination before the command runs;
use a temporary destination and replace the old file only on success when needed.
