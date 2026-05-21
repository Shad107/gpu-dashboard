#!/usr/bin/env bash
# install-coolbits-xorg.sh — install an Xorg drop-in enabling Coolbits=12
# on the NVIDIA card, so the `clock_offsets` module can drive sliders
# without sudo at runtime.
#
# What this installs:
#   /etc/X11/xorg.conf.d/20-nvidia-coolbits.conf
#
# Usage:
#   sudo bash install-coolbits-xorg.sh [--bus-id PCI:X:X:X] [--headless] [--check] [--print]
#
# Flags:
#   --bus-id <id>   PCIe bus in Xorg format (default: PCI:1:0:0 → 01:00.0).
#                   For headless setups (eGPU / VM passthrough) pass the
#                   actual bus from `lspci | grep -i nvidia` then converted.
#   --headless      Add ConnectedMonitor/AllowEmptyInitialConfiguration options
#                   so Xorg can start without a real display attached.
#   --check         exit 0 if Coolbits=12 already configured, 1 otherwise.
#   --print         print what would be installed, don't write anything.
#   -h, --help      show this help.
#
# Takes effect after the next X server restart (logout/login or reboot).

set -euo pipefail

CONF_PATH="/etc/X11/xorg.conf.d/20-nvidia-coolbits.conf"
BUS_ID="PCI:1:0:0"
HEADLESS=0
MODE="install"

while [[ $# -gt 0 ]]; do
  case "$1" in
    --bus-id)   BUS_ID="$2"; shift 2 ;;
    --headless) HEADLESS=1; shift ;;
    --check)    MODE="check"; shift ;;
    --print)    MODE="print"; shift ;;
    -h|--help)
      sed -n '2,/^set -euo/p' "$0" | sed -n 's/^# \{0,1\}//p' | head -n -1
      exit 0 ;;
    *) echo "Unknown arg: $1" >&2; exit 2 ;;
  esac
done

build_conf() {
  if [[ $HEADLESS -eq 1 ]]; then
    cat <<EOF
Section "ServerFlags"
    Option "AutoAddGPU" "false"
    Option "AutoBindGPU" "false"
EndSection

Section "ServerLayout"
    Identifier "Layout0"
    Screen 0 "Screen0"
EndSection

Section "Monitor"
    Identifier "Monitor0"
    HorizSync   28.0 - 33.0
    VertRefresh 43.0 - 72.0
EndSection

Section "Device"
    Identifier "Nvidia GPU"
    Driver "nvidia"
    BusID "$BUS_ID"
    Option "Coolbits" "12"
    Option "AllowEmptyInitialConfiguration" "true"
    Option "ConnectedMonitor" "DFP-0"
    Option "UseDisplayDevice" "DFP-0"
EndSection

Section "Screen"
    Identifier "Screen0"
    Device "Nvidia GPU"
    Monitor "Monitor0"
    DefaultDepth 24
    SubSection "Display"
        Depth 24
        Modes "1920x1080" "1024x768"
    EndSubSection
EndSection
EOF
  else
    cat <<EOF
Section "Device"
    Identifier "Nvidia GPU"
    Driver "nvidia"
    BusID "$BUS_ID"
    Option "Coolbits" "12"
EndSection
EOF
  fi
}

# ── check mode ───────────────────────────────────────────────────────────────
if [[ "$MODE" == "check" ]]; then
  # Considère Coolbits configuré si AU MOINS un fichier xorg.conf.d/*.conf
  # OU /etc/X11/xorg.conf contient Option "Coolbits" "<n>" avec n & 8 ou n & 12
  for f in /etc/X11/xorg.conf /etc/X11/xorg.conf.d/*.conf; do
    [[ -f "$f" ]] || continue
    if grep -qE 'Option *"Coolbits" *"(8|12|24|28)"' "$f" 2>/dev/null; then
      exit 0
    fi
  done
  exit 1
fi

# ── print mode ───────────────────────────────────────────────────────────────
if [[ "$MODE" == "print" ]]; then
  echo "Would install $CONF_PATH (mode 0644):"
  echo "──────────────────────────────────────────────"
  build_conf
  echo "──────────────────────────────────────────────"
  exit 0
fi

# ── install mode (default) ───────────────────────────────────────────────────
if [[ $EUID -ne 0 ]]; then
  echo "ERROR: this script needs root. Run with: sudo bash $0" >&2
  exit 1
fi

mkdir -p /etc/X11/xorg.conf.d
echo "→ Writing $CONF_PATH"
build_conf > "$CONF_PATH"
chmod 0644 "$CONF_PATH"

echo "✓ Coolbits Xorg drop-in installed."
echo "  Takes effect after the next X server restart (logout/login or reboot)."
if [[ $HEADLESS -eq 1 ]]; then
  echo
  echo "⚠ Headless mode: an X server still needs to be started on the NVIDIA."
  echo "  See docs/HEADLESS_SETUP.md for the sddm + autologin pattern."
fi
