#!/usr/bin/env bash
# install-oculink-watchdog.sh — install a tiny systemd service that monitors
# `nvidia-smi` responsiveness and logs drops/recoveries. Useful for eGPU/OcuLink
# setups where the link can become unresponsive under load.
#
# What this installs:
#   /usr/local/bin/gpu-oculink-watchdog.sh   — the polling loop
#   /etc/systemd/system/gpu-oculink-watchdog.service
#
# Usage:
#   sudo bash install-oculink-watchdog.sh [--interval 60] [--log <path>] [--check] [--print]
#
# Flags:
#   --interval <s>   polling interval in seconds (default: 60).
#   --log <path>     log file path (default: /var/log/gpu-oculink-watchdog.log).
#   --check          exit 0 if service is installed AND active, 1 otherwise.
#   --print          print what would be installed, don't write anything.
#   -h, --help       show this help.

set -euo pipefail

WATCHDOG_PATH="/usr/local/bin/gpu-oculink-watchdog.sh"
SERVICE_PATH="/etc/systemd/system/gpu-oculink-watchdog.service"
INTERVAL=60
LOG_FILE="/var/log/gpu-oculink-watchdog.log"
MODE="install"

while [[ $# -gt 0 ]]; do
  case "$1" in
    --interval) INTERVAL="$2"; shift 2 ;;
    --log)      LOG_FILE="$2"; shift 2 ;;
    --check)    MODE="check"; shift ;;
    --print)    MODE="print"; shift ;;
    -h|--help)
      sed -n '2,/^set -euo/p' "$0" | sed -n 's/^# \{0,1\}//p' | head -n -1
      exit 0 ;;
    *) echo "Unknown arg: $1" >&2; exit 2 ;;
  esac
done

build_watchdog() {
  cat <<EOF
#!/usr/bin/env bash
# /usr/local/bin/gpu-oculink-watchdog.sh — installed by gpu-dashboard.
# Polls nvidia-smi; logs drop/recover state changes to LOG_FILE.
set -e
LOG_FILE="$LOG_FILE"
STATE="up"
while true; do
  if timeout 5 nvidia-smi -q -d PIDS >/dev/null 2>&1; then
    if [[ "\$STATE" == "down" ]]; then
      echo "\$(date '+%Y-%m-%d %H:%M:%S') state=up GPU recovered" >> "\$LOG_FILE"
      STATE="up"
    else
      echo "\$(date '+%Y-%m-%d %H:%M:%S') heartbeat state=up" >> "\$LOG_FILE"
    fi
  else
    if [[ "\$STATE" == "up" ]]; then
      echo "\$(date '+%Y-%m-%d %H:%M:%S') state=down DROP nvidia-smi not responding" >> "\$LOG_FILE"
      STATE="down"
    fi
  fi
  sleep $INTERVAL
done
EOF
}

build_unit() {
  cat <<EOF
[Unit]
Description=GPU OcuLink watchdog (gpu-dashboard)
After=network.target

[Service]
Type=simple
ExecStart=$WATCHDOG_PATH
Restart=on-failure
RestartSec=10

[Install]
WantedBy=multi-user.target
EOF
}

# ── check mode ───────────────────────────────────────────────────────────────
if [[ "$MODE" == "check" ]]; then
  [[ -x "$WATCHDOG_PATH" && -f "$SERVICE_PATH" ]] || exit 1
  systemctl is-active --quiet gpu-oculink-watchdog || exit 1
  exit 0
fi

# ── print mode ───────────────────────────────────────────────────────────────
if [[ "$MODE" == "print" ]]; then
  echo "Would install $WATCHDOG_PATH (mode 0755):"
  echo "──────────────────────────────────────────────"
  build_watchdog
  echo "──────────────────────────────────────────────"
  echo
  echo "Would install $SERVICE_PATH (mode 0644):"
  echo "──────────────────────────────────────────────"
  build_unit
  echo "──────────────────────────────────────────────"
  exit 0
fi

# ── install mode (default) ───────────────────────────────────────────────────
if [[ $EUID -ne 0 ]]; then
  echo "ERROR: this script needs root. Run with: sudo bash $0" >&2
  exit 1
fi

echo "→ Writing $WATCHDOG_PATH"
build_watchdog > "$WATCHDOG_PATH"
chmod 0755 "$WATCHDOG_PATH"

echo "→ Writing $SERVICE_PATH"
build_unit > "$SERVICE_PATH"
chmod 0644 "$SERVICE_PATH"

# Ensure log file exists with correct permissions
touch "$LOG_FILE"
chmod 0644 "$LOG_FILE"

echo "→ systemctl daemon-reload + enable + start"
systemctl daemon-reload
systemctl enable --now gpu-oculink-watchdog

echo "✓ OcuLink watchdog installed and running. Tail the log:"
echo "  tail -f $LOG_FILE"
