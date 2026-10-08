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

```bash
demo() {
    explain 'Set up a value, then use it in the next command.'
    run 'export GREETING="Hello, demo"'
    run 'printf "%s\n" "$GREETING" | sort'

    explain 'This writes a file in the current directory.'
    run 'printf "%s\n" "$GREETING" > demo.txt'
    run 'cat demo.txt'

    explain 'Multiline snippets and heredocs also work.'
    run 'cat <<EOF
Greeting: $GREETING
EOF'
}
```

Each `run` displays its literal snippet, waits for **Enter**, then executes it.
Type **q** (or **Q**) and Enter to quit. Other input repeats the prompt and is
never evaluated. End-of-input quits without executing the pending command.
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
- Output is deliberately plain text, with no colour dependencies, clearing,
  animation, replay or automatic execution mode.
- Completion, q and EOF return 0, including intentional command failures.
  Invalid CLI arguments return 2. `-h` / `--help` prints local usage without
  executing the demo. An explicit `exit N` in a snippet retains that status.
