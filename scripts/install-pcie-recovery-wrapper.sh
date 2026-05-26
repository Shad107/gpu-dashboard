#!/usr/bin/env bash
# install-pcie-recovery-wrapper.sh — install the root-side wrapper + sudoers
# rule for the gpu-dashboard PCIe recovery wizard.
#
# What this installs:
#   /usr/local/bin/gpu-dashboard-pcie-recover  — bash wrapper that accepts
#                                                ONE whitelisted step id and
#                                                runs the corresponding root
#                                                command. Refuses anything
#                                                else.
#   /etc/sudoers.d/gpu-dashboard-pcie-recover  — NOPASSWD rule scoped to that
#                                                wrapper only.
#
# Allowed step ids:
#   persistence_restart   systemctl restart nvidia-persistenced + nvidia-smi -pm 1
#   module_reload         rmmod + modprobe nvidia*
#   pcie_rescan           echo 1 > /sys/.../<bdf>/remove then /sys/bus/pci/rescan
#   flr                   echo 1 > /sys/.../<bdf>/reset
#
# The wrapper validates the BDF (must be a real NVIDIA device under
# /sys/bus/pci/devices/) — refuses arbitrary writes anywhere else.
#
# Usage:
#   sudo bash install-pcie-recovery-wrapper.sh [--user <name>] [--check] [--print]

set -euo pipefail

WRAPPER_PATH="/usr/local/bin/gpu-dashboard-pcie-recover"
SUDOERS_PATH="/etc/sudoers.d/gpu-dashboard-pcie-recover"
TARGET_USER="${SUDO_USER:-${USER:-}}"
MODE="install"

while [[ $# -gt 0 ]]; do
  case "$1" in
    --user)   TARGET_USER="$2"; shift 2 ;;
    --check)  MODE="check"; shift ;;
    --print)  MODE="print"; shift ;;
    -h|--help)
      sed -n '2,/^set -euo/p' "$0" | sed -n 's/^# \{0,1\}//p' | head -n -1
      exit 0 ;;
    *) echo "Unknown arg: $1" >&2; exit 2 ;;
  esac
done

read -r -d '' WRAPPER_CONTENT <<'EOF' || true
#!/usr/bin/env bash
# /usr/local/bin/gpu-dashboard-pcie-recover — installed by gpu-dashboard.
# Whitelist-only PCIe recovery commands. Refuses anything outside the
# four canonical recovery steps.
set -e

STEP="${1:-}"
BDF="${2:-}"

# F5.4 — tee everything to a persistent log file so the operator
# can `tail -f /tmp/gpu-dashboard-recovery.log` from a terminal
# and follow progress even if the step kills the browser running
# the dashboard.
LOG=/tmp/gpu-dashboard-recovery.log
{
  printf '\n──────── %s — step=%s bdf=%s ────────\n' \
    "$(date +'%Y-%m-%d %H:%M:%S')" "$STEP" "${BDF:-—}"
} | tee -a "$LOG" >/dev/null
exec > >(tee -a "$LOG") 2>&1
chmod 666 "$LOG" 2>/dev/null || true

die() { echo "ERROR: $*" >&2; exit 2; }

# Validate BDF if provided: must be a real NVIDIA device under /sys.
validate_bdf() {
  [[ -n "$BDF" ]] || die "BDF required for step '$STEP'"
  [[ "$BDF" =~ ^[0-9a-f]{4}:[0-9a-f]{2}:[0-9a-f]{2}\.[0-9]$ ]] || \
    die "BDF must match DDDD:BB:DD.F, got: '$BDF'"
  [[ -d "/sys/bus/pci/devices/$BDF" ]] || die "no such PCI device: $BDF"
  local vendor
  vendor=$(cat "/sys/bus/pci/devices/$BDF/vendor" 2>/dev/null || echo "")
  [[ "$vendor" == "0x10de" ]] || die "BDF $BDF is not an NVIDIA device (vendor=$vendor)"
}

case "$STEP" in
  persistence_restart)
    echo "→ Restarting nvidia-persistenced"
    systemctl restart nvidia-persistenced 2>/dev/null || \
      /usr/bin/nvidia-persistenced 2>/dev/null || true
    sleep 1
    echo "→ Enabling persistence mode"
    /usr/bin/nvidia-smi -pm 1 || true
    echo "✓ persistence_restart done"
    ;;
  module_reload)
    echo "→ Killing GPU consumers"
    fuser -k /dev/nvidia* 2>/dev/null || true
    sleep 1
    echo "→ Removing modules"
    rmmod nvidia_uvm 2>/dev/null || true
    rmmod nvidia_drm 2>/dev/null || true
    rmmod nvidia_modeset 2>/dev/null || true
    rmmod nvidia 2>/dev/null || true
    sleep 1
    echo "→ Loading modules"
    modprobe nvidia
    modprobe nvidia_modeset
    modprobe nvidia_uvm
    modprobe nvidia_drm 2>/dev/null || true
    echo "✓ module_reload done"
    ;;
  pcie_rescan)
    validate_bdf
    echo "→ Removing $BDF from PCIe enumeration"
    echo 1 > "/sys/bus/pci/devices/$BDF/remove"
    sleep 2
    echo "→ Triggering bus rescan"
    echo 1 > /sys/bus/pci/rescan
    sleep 3
    echo "✓ pcie_rescan done"
    ;;
  flr)
    validate_bdf
    [[ -e "/sys/bus/pci/devices/$BDF/reset" ]] || \
      die "FLR not supported (no /sys/.../reset for $BDF)"
    echo "→ Function Level Reset on $BDF"
    echo 1 > "/sys/bus/pci/devices/$BDF/reset"
    sleep 2
    echo "✓ flr done"
    ;;
  *)
    die "unknown step: '$STEP'. allowed: persistence_restart, module_reload, pcie_rescan, flr"
    ;;
esac
EOF

if [[ -z "$TARGET_USER" || "$TARGET_USER" == "root" ]]; then
  echo "ERROR: cannot determine non-root user. Pass --user <name>." >&2
  exit 2
fi

read -r -d '' SUDOERS_CONTENT <<EOF || true
# /etc/sudoers.d/gpu-dashboard-pcie-recover — installed by gpu-dashboard.
# Grants passwordless sudo to ONE specific binary, nothing else.
$TARGET_USER ALL=(root) NOPASSWD: $WRAPPER_PATH
EOF

if [[ "$MODE" == "check" ]]; then
  [[ -x "$WRAPPER_PATH" ]] || exit 1
  sudo -n -l "$WRAPPER_PATH" >/dev/null 2>&1 || exit 1
  exit 0
fi

if [[ "$MODE" == "print" ]]; then
  echo "Would install $WRAPPER_PATH (mode 0755):"
  echo "──────────────────────────────────────────────"
  echo "$WRAPPER_CONTENT"
  echo "──────────────────────────────────────────────"
  echo
  echo "Would install $SUDOERS_PATH (mode 0440):"
  echo "──────────────────────────────────────────────"
  echo "$SUDOERS_CONTENT"
  echo "──────────────────────────────────────────────"
  exit 0
fi

if [[ $EUID -ne 0 ]]; then
  echo "ERROR: this script needs root. Run with: sudo bash $0 --user $TARGET_USER" >&2
  exit 1
fi

echo "→ Writing $WRAPPER_PATH"
printf '%s\n' "$WRAPPER_CONTENT" > "$WRAPPER_PATH"
chmod 0755 "$WRAPPER_PATH"

echo "→ Writing $SUDOERS_PATH (user: $TARGET_USER)"
printf '%s\n' "$SUDOERS_CONTENT" > "$SUDOERS_PATH"
chmod 0440 "$SUDOERS_PATH"

echo "→ Validating sudoers syntax"
if ! visudo -c -f "$SUDOERS_PATH" >/dev/null; then
  echo "ERROR: sudoers file invalid, rolling back" >&2
  rm -f "$SUDOERS_PATH"
  exit 3
fi

echo "✓ PCIe recovery wrapper installed."
echo "  Test (will only print help): sudo -n $WRAPPER_PATH"
