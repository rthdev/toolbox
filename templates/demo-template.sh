#!/usr/bin/env bash
# Copy this file, then edit the header and demo() below. Requires Bash 3.2+.

DEMO_TITLE='Workflow demo'
DEMO_DESCRIPTION='A small, read-only example to adapt for your audience.'
DEMO_PREREQUISITES='Bash 3.2+ and sort (Linux or macOS).'

demo() {
    explain 'Inspect the current directory.'
    run 'pwd'

    explain 'Pipelines work like they do in a shell.'
    run 'printf "%s\n" pear apple orange | sort'

    explain 'An intentional failure does not stop the presentation.'
    run 'false'
    run 'printf "%s\n" "The demo continues."'

    # Redirection example (uncomment to create/overwrite a file):
    # run 'printf "%s\n" "Hello, demo" > demo.txt'
    # run 'cat demo.txt'
}

# ---- Runner helpers: normally leave these unchanged. ----

run() {
    local demo_answer
    printf '\n$ %s\n' "$1"
    while true; do
        printf '[Enter] execute / [q] quit: '
        IFS= read -r demo_answer || exit 0
        case "$demo_answer" in
            '') break ;;
            q|Q) exit 0 ;;
            *) printf 'Press Enter to execute, or type q to quit.\n' ;;
        esac
    done
    # These are trusted snippets from this file, never prompt input.
    # No subshell: directory changes and exported variables persist.
    set +e
    set -o pipefail
    eval "$1"
    local demo_status=$?
    if (( demo_status != 0 )); then
        printf '\n[exit %s]\n' "$demo_status"
    fi
    return 0
}

explain() {
    printf '\n%s\n' "$1"
}

usage() {
    printf 'Usage: %s [-h|--help]\n' "${0##*/}"
    printf '%s\n' 'Edit the header and demo() in your copy, then run it with Bash.' \
        'Enter executes the displayed command; q or end-of-input quits.' \
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
    demo
fi
