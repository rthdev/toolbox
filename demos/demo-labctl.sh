#!/usr/bin/env bash

DEMO_TITLE='Labctl Workflow demo'
DEMO_DESCRIPTION='Demo about labctl - A CLI for reproducible Linux Learning Labs'
DEMO_PREREQUISITES='Bash 3.2+ and sort + all prerequisites for labctl, see github.com/rthdev/labctl'

demo() {
    wait
    clean
    section 'Installing'
    explain 'Change to a directory of your choice and checkout labctl'
    run 'cd ~/wip'
    run 'git clone https://github.com/rthdev/labctl'
    run 'cd labctl'

    explain 'Use pipx for installation'
    run 'pipx install .'
    run 'labctl version'
    wait
    clean

    section 'Images Handling'
    explain 'List current images'
    run 'labctl images'
    explain 'download rocky:9 image'
    run 'labctl image pull rocky:9'
    run 'labctl images'
    wait
    clean

    section 'Lab creation'
    explain 'List labs'
    run 'labctl labs'
    explain 'Create Lab LX001'
    run 'labctl lab create LX001'
    run 'labctl labs'
    explain 'List lab properties'
    run 'labctl lab inspect LX001'
    wait
    clean

    section 'Lab VM Handling'
    explain 'List Lab VMs'
    run 'labctl vms'
    explain 'ssh into Lab VM'
    run 'labctl vm ssh LX001 node'
    wait
    clean

    section 'Lab Grading and cleanup'
    explain 'Grade Lab'
    run 'labctl grade LX001'
    explain 'List labs'
    run 'labctl labs'
    explain 'Delete instantiated lab'
    run 'labctl lab rm --force LX001'
    run 'labctl labs'
    wait
    explain 'This concludes the demo'

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
