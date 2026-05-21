#!/usr/bin/env bash
# gpu-dashboard installer — shell wrapper around the Python installer.
# Checks basic prerequisites then delegates to `python3 -m gpu_dashboard.install`.
set -euo pipefail

cd "$(dirname "$0")"

if ! command -v python3 &>/dev/null; then
  echo "✗ Python 3 not found. Install it via your package manager first." >&2
  exit 1
fi

# Minimum version 3.9
PYVER=$(python3 -c 'import sys; print(f"{sys.version_info[0]}.{sys.version_info[1]}")')
PYMAJOR=${PYVER%%.*}
PYMINOR=${PYVER##*.}
if (( PYMAJOR < 3 )) || (( PYMAJOR == 3 && PYMINOR < 9 )); then
  echo "✗ Python $PYVER detected, but ≥3.9 required." >&2
  exit 1
fi

# jsonschema (only external dep)
if ! python3 -c 'import jsonschema' 2>/dev/null; then
  echo "⚠ Python module 'jsonschema' is not installed."
  read -rp "  Install it now via pip --user? [Y/n] " ans
  ans=${ans:-Y}
  if [[ "$ans" =~ ^[YyOo] ]]; then
    python3 -m pip install --user --break-system-packages jsonschema || {
      echo "✗ pip install failed. Install it manually:" >&2
      echo "    python3 -m pip install --user jsonschema" >&2
      exit 1
    }
  else
    echo "Cancelled. Install jsonschema then re-run this script." >&2
    exit 1
  fi
fi

# Delegate to Python for the logic
exec python3 -c "
import sys
sys.path.insert(0, 'src')
from gpu_dashboard.install import main
sys.exit(main(sys.argv[1:]))
" "$@"
