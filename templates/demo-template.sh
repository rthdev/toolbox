#!/usr/bin/env bash
# Copy this file, then edit the header and demo() below. Requires Bash 3.2+.

DEMO_TITLE='Workflow demo'
DEMO_DESCRIPTION='A small, read-only example to adapt for your audience.'
DEMO_PREREQUISITES='Bash 3.2+ and sort (Linux or macOS).'

demo() {
    section 'Inspect the workspace'
    explain 'Inspect the current directory.'
    run 'pwd'

    explain 'Pipelines work like they do in a shell.'
    run 'printf "%s\n" pear apple orange | sort'

    wait                 # Discuss the output before clearing it.
    clean                # Immediate: omit wait above if no pause is wanted.
    section 'Expected failures'
    explain 'An intentional failure does not stop the presentation.'
    run 'false'
    run 'printf "%s\n" "The demo continues."'

    # Redirection example (uncomment to create/overwrite a file):
    # run 'printf "%s\n" "Hello, demo" > demo.txt'
    # run 'cat demo.txt'
}

# ---- Runner helpers: normally leave these unchanged. ----

STYLE_BOLD=''
STYLE_MUTED=''
STYLE_RESET=''
if [[ -t 1 && -n ${TERM:-} && ${TERM:-} != dumb && -z ${NO_COLOR+x} ]]; then
    STYLE_BOLD=$'\033[1m'
    STYLE_MUTED=$'\033[2m'
    STYLE_RESET=$'\033[0m'
fi

# Clear the visible screen, not scrollback; never read input here.
clean() {
    if [[ -t 1 && -n ${TERM:-} && ${TERM:-} != dumb ]]; then
        printf '\033[2J\033[H'
    fi
    return 0
}

section() {
    printf '\n%s== %s ==%s\n' "$STYLE_BOLD" "$1" "$STYLE_RESET"
}

# Intentionally shadows Bash's wait; use `builtin wait` for background jobs.
wait() {
    local demo_answer
    while true; do
        if ! IFS= read -r -s demo_answer; then
            printf '\n'
            exit 0
        fi
        case "$demo_answer" in
            '') printf '\n'; return 0 ;;
            q|Q) printf '\n'; exit 0 ;;
            *) printf '\nPress Enter to continue, or type q to quit: ' ;;
        esac
    done
}

run() {
    # Prefix continuation lines for display only; evaluate the original snippet.
    local demo_display=${1//$'\n'/$'\n> '}
    printf '\n%s$ %s%s' "$STYLE_BOLD" "$demo_display" "$STYLE_RESET"
    wait
    # These are trusted snippets from this file, never prompt input.
    # No subshell: directory changes and exported variables persist.
    set +e
    set -o pipefail
    eval "$1"
    local demo_status=$?
    if (( demo_status != 0 )); then
        printf '\n%s[exit %s]%s\n' "$STYLE_MUTED" "$demo_status" "$STYLE_RESET"
    fi
    return 0
}

explain() {
    printf '\n%s%s%s\n' "$STYLE_MUTED" "$1" "$STYLE_RESET"
}

usage() {
    printf 'Usage: %s [-h|--help]\n' "${0##*/}"
    printf '%s\n' 'Edit the header and demo() in your copy, then run it with Bash.' \
        'Enter executes a command or ends wait; q or end-of-input quits.' \
        'clean clears immediately; section prints a heading without waiting.' \
        'Command failures are displayed and ignored. Completed actions are not undone.'
}

if [[ ${BASH_SOURCE[0]} == "$0" ]]; then
    if (( $# == 1 )) && [[ $1 == -h || $1 == --help ]]; then
        usage
        exit 0
    elif (( $# != 0 )); then
        usage >&2
        exit 2
    fi
    printf '%s\n%s\nPrerequisites: %s\n' \
        "$DEMO_TITLE" "$DEMO_DESCRIPTION" "$DEMO_PREREQUISITES"
    printf '\n%sEnter: continue / execute command; q + Enter: quit%s\n' \
        "$STYLE_MUTED" "$STYLE_RESET"
    demo
fi
