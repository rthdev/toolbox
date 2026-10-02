#!/bin/bash
# Shared CLI/transport for certificate inspection, not trust verification.
tls_usage() {
    printf 'Usage: %s [--timeout SECONDS] [--servername NAME] HOST[:PORT]\n' "${0##*/}"
    printf '%s\n' 'IPv6: [ADDRESS][:PORT]. Port defaults to 443; timeout to 10 seconds.'
    printf '%s\n' 'SECONDS is an integer from 1 to 999999 (plus a 1-second kill grace).'
    printf '%s\n' 'DNS targets send SNI; IP targets do not. --servername NAME overrides SNI.'
    printf '%s\n' 'Inspect only: self-signed and expired certificates are accepted.'
    printf '%s\n' 'Exit codes: 0 success, 1 operational failure, 2 usage error.'
}

tls_bad_usage() {
    printf '%s: %s\n' "${0##*/}" "$1" >&2
    tls_usage >&2
    exit 2
}

tls_ipv6_valid() {
    local ip=$1 part tail compressed=0
    local -a groups octets
    # Embedded IPv4 occupies two hextets.
    if [[ $ip == *.* ]]; then
        tail=${ip##*:}
        [[ $tail =~ ^[0-9]{1,3}(\.[0-9]{1,3}){3}$ ]] || return 1
        IFS=. read -r -a octets <<< "$tail"
        for part in "${octets[@]}"; do
            ((10#$part <= 255)) || return 1
        done
        ip=${ip%:*}:0:0
    fi
    [[ $ip != *:::* ]] || return 1
    if [[ $ip == *::* ]]; then
        tail=${ip#*::}
        [[ $tail != *::* ]] || return 1
        compressed=1
        ip=${ip/::/:}
        ip=${ip#:}
        ip=${ip%:}
    else
        [[ $ip != :* && $ip != *: ]] || return 1
    fi
    IFS=: read -r -a groups <<< "$ip"
    for part in "${groups[@]}"; do
        [[ $part =~ ^[0-9a-fA-F]{1,4}$ ]] || return 1
    done
    if ((compressed)); then
        ((${#groups[@]} < 8))
    else
        ((${#groups[@]} == 8))
    fi
}

tls_args() {
    TLS_TIMEOUT=10
    TLS_SERVERNAME=''
    local target='' port=443
    while (($#)); do
        case $1 in
            -h|--help) tls_usage; exit 0 ;;
            --timeout|--servername)
                (($# >= 2)) || tls_bad_usage "missing value for $1"
                if [[ $1 == --timeout ]]; then
                    if [[ ! $2 =~ ^[0-9]{1,6}$ ]] || ((10#$2 == 0)); then
                        tls_bad_usage 'invalid timeout'
                    fi
                    TLS_TIMEOUT=$((10#$2))
                else
                    [[ $2 =~ ^[a-zA-Z0-9_][a-zA-Z0-9_.-]*$ ]] || tls_bad_usage 'invalid servername'
                    TLS_SERVERNAME=$2
                fi
                shift 2 ;;
            --)
                shift
                if [[ -n $target ]] || (($# != 1)); then
                    tls_bad_usage 'expected one target'
                fi
                target=$1
                shift ;;
            -*) tls_bad_usage "unknown option: $1" ;;
            *) [[ -z $target ]] || tls_bad_usage 'expected one target'; target=$1; shift ;;
        esac
    done
    [[ -n $target ]] || tls_bad_usage 'missing target'
    if [[ $target =~ ^\[([0-9a-fA-F:.]+)\](:([0-9]+))?$ ]]; then
        TLS_HOST=${BASH_REMATCH[1]}
        [[ -z ${BASH_REMATCH[2]} ]] || port=${BASH_REMATCH[3]}
        tls_ipv6_valid "$TLS_HOST" || tls_bad_usage 'invalid IPv6 address'
        TLS_CONNECT="[$TLS_HOST]"
    elif [[ $target =~ ^([a-zA-Z0-9_][a-zA-Z0-9_.-]*)(:([0-9]+))?$ ]]; then
        TLS_HOST=${BASH_REMATCH[1]}
        [[ -z ${BASH_REMATCH[2]} ]] || port=${BASH_REMATCH[3]}
        TLS_CONNECT=$TLS_HOST
    else
        tls_bad_usage 'invalid target (IPv6 must be bracketed)'
    fi
    if [[ ! $port =~ ^[0-9]{1,5}$ ]] || ((10#$port < 1 || 10#$port > 65535)); then
        tls_bad_usage 'invalid port'
    fi
    TLS_PORT=$((10#$port))
    TLS_CONNECT+=":$TLS_PORT"
}

tls_error() {
    printf '%s: %s\n' "${0##*/}" "$*" >&2
    exit 1
}

tls_fetch() {
    local dep status=0
    for dep in openssl timeout mktemp; do
        command -v "$dep" >/dev/null || tls_error "missing dependency: $dep"
    done
    local -a sni=(-noservername)
    if [[ -n $TLS_SERVERNAME ]]; then
        sni=(-servername "$TLS_SERVERNAME")
    elif [[ $TLS_HOST != *:* && ! $TLS_HOST =~ ^[0-9.]+$ ]]; then
        sni=(-servername "$TLS_HOST")
    fi
    TLS_WORK=$(mktemp -d "${TMPDIR:-/tmp}/tls.XXXXXXXX") || tls_error 'cannot create temporary directory'
    trap 'rm -rf -- "$TLS_WORK"' EXIT
    trap 'exit 1' HUP INT TERM
    timeout --kill-after=1 "$TLS_TIMEOUT" openssl s_client \
        -connect "$TLS_CONNECT" "${sni[@]}" -showcerts </dev/null \
        >"$TLS_WORK/peer" 2>"$TLS_WORK/diagnostics" || status=$?
    if ((status == 124 || status == 137)); then
        tls_error "connection to $TLS_CONNECT timed out after $TLS_TIMEOUT seconds"
    fi
    if ((status != 0)); then
        cat "$TLS_WORK/diagnostics" >&2
        tls_error "TLS connection to $TLS_CONNECT failed (status $status)"
    fi
}
