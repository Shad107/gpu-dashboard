#!/usr/bin/env bash
# F2.3 — install an apt dpkg hook that auto-snapshots system state
# before AND after every apt operation, so the user always has a
# "before" snapshot to diff against when something breaks after an
# upgrade.
#
# The hook is a one-liner that POSTs to the dashboard's
# /api/witness/take endpoint. Failure is silent (|| true) so a
# stopped dashboard never blocks `apt upgrade`.
#
# Usage:
#   sudo bash scripts/install-witness-dpkg-hook.sh [--check] [--print] [--uninstall]
#
# --check     exit 0 if installed (hook file exists), 1 otherwise
# --print     show what would be written, do nothing
# --uninstall remove the hook file
# (default)   write the hook file
set -euo pipefail

HOOK_FILE="/etc/apt/apt.conf.d/99-gpu-dashboard-witness"
DASHBOARD_URL="http://127.0.0.1:9999"

read -r -d '' HOOK_CONTENT <<EOF || true
// Auto-snapshot system state before/after every apt operation.
// Installed by gpu-dashboard (scripts/install-witness-dpkg-hook.sh).
// Failure is non-fatal: if the dashboard isn't running the hook
// just exits 0 and apt continues normally.

DPkg::Pre-Invoke {
    "curl -sf -m 5 -o /dev/null -X POST -H 'Content-Type: application/json' --data '{\"reason\":\"apt_pre\"}' ${DASHBOARD_URL}/api/witness/take || true";
};

DPkg::Post-Invoke {
    "curl -sf -m 5 -o /dev/null -X POST -H 'Content-Type: application/json' --data '{\"reason\":\"apt_post\"}' ${DASHBOARD_URL}/api/witness/take || true";
};
EOF

action="install"
for arg in "$@"; do
    case "$arg" in
        --check)     action="check" ;;
        --print)     action="print" ;;
        --uninstall) action="uninstall" ;;
        -h|--help)
            sed -n '1,18p' "$0"
            exit 0
            ;;
        *)
            echo "Unknown option: $arg" >&2
            exit 2
            ;;
    esac
done

case "$action" in
    check)
        [ -f "$HOOK_FILE" ] && exit 0 || exit 1
        ;;
    print)
        echo "Would write to: $HOOK_FILE"
        echo "---"
        echo "$HOOK_CONTENT"
        exit 0
        ;;
    uninstall)
        if [ -f "$HOOK_FILE" ]; then
            rm -f "$HOOK_FILE"
            echo "✓ Removed $HOOK_FILE"
        else
            echo "Not installed: $HOOK_FILE"
        fi
        exit 0
        ;;
    install)
        if [ "$EUID" -ne 0 ]; then
            echo "Must run as root (apt config is root-owned)." >&2
            exit 1
        fi
        if ! command -v curl >/dev/null 2>&1; then
            echo "curl not found — required for the hook to call the dashboard." >&2
            exit 1
        fi
        echo "$HOOK_CONTENT" > "$HOOK_FILE"
        chmod 0644 "$HOOK_FILE"
        echo "✓ Installed $HOOK_FILE"
        echo
        echo "The next 'apt upgrade' will:"
        echo "  Pre-Invoke  → POST /api/witness/take with reason=apt_pre"
        echo "  Post-Invoke → POST /api/witness/take with reason=apt_post"
        echo
        echo "If the dashboard isn't running at the time, the hook silently exits 0."
        ;;
esac
