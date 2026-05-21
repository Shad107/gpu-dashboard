#!/usr/bin/env bash
# install-power-limit-wrapper.sh — install the root-side wrapper + sudoers rule
# for the gpu-dashboard `power_limit` module.
#
# What this installs:
#   /usr/local/bin/set-power-limit  — bash wrapper around `nvidia-smi -pl`,
#                                     validates argument (100-350 W).
#   /etc/sudoers.d/gpu-dashboard    — NOPASSWD rule scoped to that wrapper only.
#
# Usage:
#   sudo bash install-power-limit-wrapper.sh [--user <name>] [--check] [--print]
#
# Flags:
#   --user <name>   user that should get passwordless sudo for the wrapper.
#                   Defaults to $SUDO_USER (when run via sudo) or $USER.
#   --check         exit 0 if already installed, 1 otherwise. No changes.
#   --print         print what would be installed, don't write anything.
#   -h, --help      show this help.
#
# Re-running is safe: existing files are overwritten with the same content,
# sudoers is re-validated each time.

set -euo pipefail

WRAPPER_PATH="/usr/local/bin/set-power-limit"
SUDOERS_PATH="/etc/sudoers.d/gpu-dashboard"
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
# /usr/local/bin/set-power-limit — installed by gpu-dashboard.
# Validates argument then calls `nvidia-smi -pl`. Persists value for boot.
set -e
W="$1"
if ! [[ "$W" =~ ^[0-9]+$ ]] || (( W < 100 )) || (( W > 350 )); then
  echo "ERROR: argument must be an integer between 100 and 350 (watts), got: '$W'" >&2
  exit 2
fi
STATE=/etc/default/gpu-powerlimit
TMP=$(mktemp)
echo "WATTS=$W" > "$TMP"
mv "$TMP" "$STATE"
chmod 0644 "$STATE"
exec /usr/bin/nvidia-smi -pl "$W"
EOF

if [[ -z "$TARGET_USER" || "$TARGET_USER" == "root" ]]; then
  echo "ERROR: cannot determine non-root user. Pass --user <name>." >&2
  exit 2
fi

read -r -d '' SUDOERS_CONTENT <<EOF || true
# /etc/sudoers.d/gpu-dashboard — installed by gpu-dashboard.
# Grants passwordless sudo to ONE specific binary, nothing else.
$TARGET_USER ALL=(root) NOPASSWD: $WRAPPER_PATH
EOF

# ── check mode ───────────────────────────────────────────────────────────────
# Le wrapper doit exister ET être appelable via sudo sans password.
# `sudo -n -l <cmd>` interroge les sudoers sans exécuter la commande, et marche
# pour un utilisateur non-root (contrairement à grep dans /etc/sudoers.d/*
# qui est en 0440 root).
if [[ "$MODE" == "check" ]]; then
  [[ -x "$WRAPPER_PATH" ]] || exit 1
  sudo -n -l "$WRAPPER_PATH" >/dev/null 2>&1 || exit 1
  exit 0
fi

# ── print mode ───────────────────────────────────────────────────────────────
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

# ── install mode (default) ───────────────────────────────────────────────────
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

echo "✓ Power-limit wrapper installed. Test with: sudo -n $WRAPPER_PATH 250"
