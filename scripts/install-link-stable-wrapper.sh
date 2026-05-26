#!/usr/bin/env bash
# F7 — Link Stable Mode wrapper.
#
# Installs /usr/local/bin/gpu-dashboard-link-stable with a sudoers
# entry that allows the dashboard user to run only:
#   enable <min_mhz> <max_mhz>   →  nvidia-smi -pm 1 && nvidia-smi --lock-gpu-clocks=A,B
#   disable                       →  nvidia-smi --reset-gpu-clocks
#   status                        →  nvidia-smi --query-gpu=...
#
# Arguments are integer-validated inside the wrapper, so even if the
# dashboard is compromised the worst it can do is lock the GPU
# clocks within a sane MHz range.
set -euo pipefail

usage() {
    sed -n '2,16p' "$0"
}

action="install"
target_user=""
for arg in "$@"; do
    case "$arg" in
        --check)         action="check" ;;
        --print)         action="print" ;;
        --uninstall)     action="uninstall" ;;
        --user)          ;;  # consume here, value next
        --user=*)        target_user="${arg#--user=}" ;;
        -h|--help)       usage; exit 0 ;;
        *)
            if [ "${prev_arg:-}" = "--user" ]; then
                target_user="$arg"
            fi
            ;;
    esac
    prev_arg="$arg"
done

WRAPPER_PATH="/usr/local/bin/gpu-dashboard-link-stable"
SUDOERS_PATH="/etc/sudoers.d/gpu-dashboard-link-stable"
SAFE_MIN_MHZ=200
SAFE_MAX_MHZ=3000

read -r -d '' WRAPPER_BODY <<'WRAPPER_EOF' || true
#!/usr/bin/env bash
# gpu-dashboard-link-stable
# Whitelisted thin wrapper around `nvidia-smi` clock locking.
# Installed by gpu-dashboard scripts/install-link-stable-wrapper.sh.
set -euo pipefail

SAFE_MIN_MHZ=200
SAFE_MAX_MHZ=3000

usage() {
    echo "usage: gpu-dashboard-link-stable {enable MIN MAX | disable | status}" >&2
    exit 2
}

[ $# -ge 1 ] || usage

cmd="$1"; shift

case "$cmd" in
    enable)
        [ $# -eq 2 ] || usage
        for arg in "$1" "$2"; do
            case "$arg" in
                ''|*[!0-9]*) echo "non-integer arg: $arg" >&2; exit 3 ;;
            esac
            if [ "$arg" -lt $SAFE_MIN_MHZ ] || [ "$arg" -gt $SAFE_MAX_MHZ ]; then
                echo "$arg out of safe range [$SAFE_MIN_MHZ,$SAFE_MAX_MHZ]" >&2
                exit 4
            fi
        done
        if [ "$1" -gt "$2" ]; then
            echo "min ($1) > max ($2)" >&2
            exit 5
        fi
        /usr/bin/nvidia-smi -pm 1
        /usr/bin/nvidia-smi --lock-gpu-clocks="$1,$2"
        ;;
    disable)
        [ $# -eq 0 ] || usage
        /usr/bin/nvidia-smi --reset-gpu-clocks
        ;;
    status)
        /usr/bin/nvidia-smi --query-gpu=clocks.gr,clocks.max.gr,persistence_mode,pstate \
            --format=csv,noheader,nounits
        ;;
    *)
        usage
        ;;
esac
WRAPPER_EOF

build_sudoers() {
    local user="$1"
    cat <<SUDOERS_EOF
# gpu-dashboard — Link Stable Mode wrapper
# Allows the dashboard user to lock/unlock GPU clocks without an
# interactive password prompt. The wrapper validates inputs.
${user} ALL=(root) NOPASSWD: ${WRAPPER_PATH}
SUDOERS_EOF
}

case "$action" in
    check)
        [ -x "$WRAPPER_PATH" ] && [ -f "$SUDOERS_PATH" ] && exit 0 || exit 1
        ;;
    print)
        echo "Would install:"
        echo "  $WRAPPER_PATH"
        echo "  $SUDOERS_PATH"
        echo
        echo "--- wrapper ---"
        echo "$WRAPPER_BODY"
        echo "--- sudoers ---"
        build_sudoers "${target_user:-<USER>}"
        exit 0
        ;;
    uninstall)
        if [ "$EUID" -ne 0 ]; then
            echo "Must run as root." >&2
            exit 1
        fi
        rm -f "$WRAPPER_PATH" "$SUDOERS_PATH"
        echo "✓ Removed $WRAPPER_PATH and $SUDOERS_PATH"
        exit 0
        ;;
    install)
        if [ "$EUID" -ne 0 ]; then
            echo "Must run as root (writing to /usr/local/bin and /etc/sudoers.d)." >&2
            exit 1
        fi
        if [ -z "$target_user" ]; then
            target_user="${SUDO_USER:-${USER:-}}"
        fi
        if [ -z "$target_user" ] || [ "$target_user" = "root" ]; then
            echo "Cannot determine target user; pass --user <name>." >&2
            exit 1
        fi
        if ! command -v nvidia-smi >/dev/null 2>&1; then
            echo "nvidia-smi not found in PATH — required for the wrapper." >&2
            exit 1
        fi
        echo "$WRAPPER_BODY" > "$WRAPPER_PATH"
        chmod 0755 "$WRAPPER_PATH"
        build_sudoers "$target_user" > "$SUDOERS_PATH"
        chmod 0440 "$SUDOERS_PATH"
        if command -v visudo >/dev/null 2>&1; then
            visudo -cf "$SUDOERS_PATH" >/dev/null || {
                echo "✗ visudo rejected $SUDOERS_PATH — removing" >&2
                rm -f "$SUDOERS_PATH"
                exit 1
            }
        fi
        echo "✓ Installed $WRAPPER_PATH"
        echo "✓ Installed $SUDOERS_PATH (target user: $target_user)"
        echo
        echo "Test from $target_user shell:"
        echo "  sudo -n $WRAPPER_PATH status"
        ;;
esac
