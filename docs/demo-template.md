# Workflow demo template

Copy `templates/demo-template.sh` wherever you prepare your demos:

```bash
cp templates/demo-template.sh ~/my-demo.sh
# Edit DEMO_TITLE, DEMO_DESCRIPTION, DEMO_PREREQUISITES and demo().
bash ~/my-demo.sh
```

The runner uses Bash builtins only, with syntax targeting Bash 3.2+ on Linux and
macOS. The example pipeline also uses `sort`. Your own commands determine any
additional dependencies and permissions. Prerequisites are printed, not checked.

## Writing the sequence

Use `explain 'text'` for audience-facing commentary and `run 'command'` for each
command you want to approve. Ordinary Bash comments stay invisible.

Additional helpers let you control presentation flow explicitly:

| Helper | Behaviour |
| --- | --- |
| `section 'Title'` | Print a heading, without waiting or clearing. |
| `explain 'Text'` | Print commentary, without waiting. |
| `run 'command'` | Show the command, wait for Enter, then execute it. |
| `wait` | Wait silently for Enter before continuing; q/EOF quits. |
| `clean` | Immediately clear the visible screen, without waiting. |

Use `wait; clean` when you want time to discuss output before clearing it.
Use `clean` alone when you want to clear immediately. `clean` emits ANSI
clear-screen and cursor-home sequences on a non-dumb terminal; it does not
request scrollback erasure. Exact scrollback behaviour depends on the terminal.
With redirected output or an unset/empty/dumb `TERM`, it does nothing.

The `wait` helper deliberately shadows Bash's built-in command of the same name.
Use `builtin wait` (or `builtin wait "$pid"`) to wait for background jobs.

```bash
demo() {
    section 'Variables and pipelines'
    explain 'Set up a value, then use it in the next command.'
    run 'export GREETING="Hello, demo"'
    run 'printf "%s\n" "$GREETING" | sort'

    wait
    clean
    section 'Writing files'
    explain 'This writes a file in the current directory.'
    run 'printf "%s\n" "$GREETING" > demo.txt'
    run 'cat demo.txt'

    explain 'Multiline snippets and heredocs also work.'
    run 'cat <<EOF
Greeting: $GREETING
EOF'
}
```

Controls appear once in the initial header, not before every command. Each `run`
displays `$ command` and waits at the end of the line for **Enter**, then executes
it. Multiline snippets display `>` before continuation lines; these prefixes are
not part of the executed code. Runner input is not echoed in a terminal.
Type **q** (or **Q**) and Enter to quit from `run` or `wait`. Other input shows a
brief reminder and waits again; it is never evaluated. End-of-input quits without
executing the pending command.
Interactive commands read from the same stdin as the runner; run demos directly
in a terminal rather than piping a prefilled list of answers.

Single quotes defer variable expansion until execution. To embed single quotes in
a snippet, use shell quoting, for example `run 'printf '\''%s\n'\'' hello'`.
Multiline snippets get one approval for the whole block; use separate `run` calls
when you want separate pauses. There is no pause after output: the next command
is displayed and waits, leaving previous output visible.

## Execution and boundaries

- Snippets are trusted Bash code evaluated in the current shell, not a sandbox.
  Only put commands you authored/reviewed into the script. Do not build snippets
  from untrusted input. Displayed commands and their output may expose secrets.
- `cd`, assignments and exports persist between commands. Run the script, do not
  source it into your working shell. Source support is for testing the helpers.
- Ordinary nonzero statuses print `[exit N]` and do not fail the demo. `pipefail`
  reports a failed pipeline component even if the last component succeeds.
  A multiline block reports its final status, not every statement's status.
- Do not enable `set -e` or change runner helpers, traps or reserved `demo_*`
  helper variables from your snippets. Explicit `exit`, `exec`, fatal shell
  errors or signals can still terminate the script. This is not error isolation.
- The shipped example is read-only. The commented redirection example would
  create or overwrite `demo.txt` if enabled. Nothing rolls back or cleans up
  executed commands when you quit; add explicit cleanup steps if needed.
- Commands and section headings are bold; commentary and failure statuses are dim.
  Command output is untouched. Styling is disabled for redirected output, an
  unset/empty/dumb `TERM`, or any set `NO_COLOR` (including an empty value).
  `NO_COLOR` disables styling, not an explicit `clean` on a suitable terminal.
  There is no automatic clearing, animation, replay or automatic execution mode.
- Completion, q and EOF return 0, including intentional command failures.
  Invalid CLI arguments return 2. `-h` / `--help` prints local usage without
  executing the demo. An explicit `exit N` in a snippet retains that status.
