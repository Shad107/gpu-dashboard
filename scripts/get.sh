#!/usr/bin/env bash
# get.sh — one-line bootstrap for gpu-dashboard.
# Run with:
#   curl -fsSL https://raw.githubusercontent.com/Shad107/gpu-dashboard/main/scripts/get.sh | bash
#
# What this does (NO sudo at any point):
#   1. Verifies git + python3 (≥3.9) are available
#   2. Clones (or updates) gpu-dashboard into ~/gpu-dashboard
#   3. Installs the only Python dependency (jsonschema) via pip --user
#   4. Starts the dashboard in the background on port 9999
#   5. Prints the URL to open in your browser to finish setup via the wizard
#
# Want to read it first? View at:
#   https://raw.githubusercontent.com/Shad107/gpu-dashboard/main/scripts/get.sh
#
# Want to skip auto-start? Set NO_START=1 before running this.
# Want to clone elsewhere? Set INSTALL_DIR=/path/before running.

set -euo pipefail

REPO_URL="${REPO_URL:-https://github.com/Shad107/gpu-dashboard.git}"
INSTALL_DIR="${INSTALL_DIR:-$HOME/gpu-dashboard}"
PORT="${PORT:-9999}"

C_GREEN="\033[32m"; C_YELLOW="\033[33m"; C_RED="\033[31m"; C_BOLD="\033[1m"; C_RESET="\033[0m"

echo -e "${C_BOLD}gpu-dashboard — bootstrap${C_RESET}"
echo

# ── Prereqs ──────────────────────────────────────────────────────────────────
for cmd in git python3; do
  if ! command -v "$cmd" >/dev/null 2>&1; then
    echo -e "${C_RED}✗${C_RESET} $cmd not found — install it via your package manager:"
    if command -v apt >/dev/null; then echo "    sudo apt install git python3 python3-pip"
    elif command -v dnf >/dev/null; then echo "    sudo dnf install git python3 python3-pip"
    elif command -v pacman >/dev/null; then echo "    sudo pacman -S git python python-pip"
    fi
    exit 1
  fi
done

PYVER=$(python3 -c 'import sys; print(f"{sys.version_info[0]}.{sys.version_info[1]}")')
PY_MAJOR=${PYVER%%.*}
PY_MINOR=${PYVER##*.}
if (( PY_MAJOR < 3 )) || (( PY_MAJOR == 3 && PY_MINOR < 9 )); then
  echo -e "${C_RED}✗${C_RESET} Python $PYVER detected, ≥3.9 required."
  exit 1
fi
echo -e "${C_GREEN}✓${C_RESET} git + python3 $PYVER"

# ── Clone or update ──────────────────────────────────────────────────────────
if [[ -d "$INSTALL_DIR/.git" ]]; then
  echo -e "${C_GREEN}✓${C_RESET} repo already at $INSTALL_DIR — pulling latest"
  git -C "$INSTALL_DIR" pull --ff-only
else
  echo -e "→ cloning $REPO_URL to $INSTALL_DIR"
  git clone --depth 1 "$REPO_URL" "$INSTALL_DIR"
fi

cd "$INSTALL_DIR"

# ── jsonschema (the only Python dep) ─────────────────────────────────────────
if python3 -c 'import jsonschema' 2>/dev/null; then
  echo -e "${C_GREEN}✓${C_RESET} python jsonschema already installed"
else
  echo -e "→ installing jsonschema via pip --user"
  python3 -m pip install --user --break-system-packages jsonschema >/dev/null 2>&1 || {
    echo -e "${C_YELLOW}⚠${C_RESET} pip install failed. Try manually:"
    echo "    python3 -m pip install --user jsonschema"
    exit 1
  }
fi

# ── Start the server (unless NO_START=1) ─────────────────────────────────────
if [[ "${NO_START:-0}" == "1" ]]; then
  echo
  echo -e "${C_GREEN}✓ install complete${C_RESET} (NO_START=1 — server not launched)"
  echo
  echo "Start it manually:"
  echo "    cd $INSTALL_DIR && PYTHONPATH=src python3 -m gpu_dashboard"
  exit 0
fi

# Check port availability before starting
if (echo >/dev/tcp/127.0.0.1/$PORT) >/dev/null 2>&1; then
  echo -e "${C_YELLOW}⚠${C_RESET} port $PORT already in use. Stop the existing process or run with PORT=9998 ..."
  exit 1
fi

echo -e "→ starting gpu-dashboard on port $PORT (background)"
LOG="$INSTALL_DIR/gpu-dashboard.log"
nohup env PYTHONPATH=src DASHBOARD_PORT=$PORT python3 -m gpu_dashboard >"$LOG" 2>&1 &
SRV_PID=$!
sleep 1.5

if ! kill -0 $SRV_PID 2>/dev/null; then
  echo -e "${C_RED}✗${C_RESET} server failed to start. Log:"
  tail -20 "$LOG"
  exit 1
fi

URL="http://localhost:$PORT"
echo
echo -e "${C_BOLD}${C_GREEN}✓ gpu-dashboard is running${C_RESET} (PID $SRV_PID)"
echo
echo -e "  ${C_BOLD}Open this URL to finish setup:${C_RESET}"
echo -e "  ${C_GREEN}$URL${C_RESET}"
echo
echo "  Log file : $LOG"
echo "  Stop     : kill $SRV_PID"
echo

# Try to open the browser if a display is available
if [[ -n "${DISPLAY:-}" ]] && command -v xdg-open >/dev/null 2>&1; then
  xdg-open "$URL" >/dev/null 2>&1 &
fi
