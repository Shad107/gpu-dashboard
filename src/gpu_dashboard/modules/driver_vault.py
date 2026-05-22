"""Module driver_vault — NVIDIA driver rollback vault (R&D #16.4).

Each `apt upgrade` that bumps the NVIDIA driver can introduce a regression
(remember 535.x → 545.x ECC issue, or 560.x's GSP RPC timeouts). Recovery
typically means hunting the .run installer for the previous version on
NVIDIA's website. This module saves you that trip :

  1. Parse /var/log/apt/history.log for nvidia-driver-* install events
  2. Cache the .deb files in ~/.config/gpu-dashboard/driver-vault/
     (only the last 3 versions to bound disk usage)
  3. List vaulted versions side-by-side with the currently-installed one
  4. Generate a sudo rollback script — NEVER executes silently. User is
     responsible for running it.

stdlib only : subprocess + re + glob.
"""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import time
from typing import Optional


NAME = "driver_vault"

_VAULT_DIR = "~/.config/gpu-dashboard/driver-vault"
_APT_HISTORY = "/var/log/apt/history.log"
_APT_ARCHIVES = "/var/cache/apt/archives"
_VAULT_MAX = 3


def vault_dir() -> str:
    return os.path.expanduser(_VAULT_DIR)


def current_driver() -> Optional[dict]:
    """Run `dpkg-query -W` for installed nvidia-driver-* packages."""
    try:
        r = subprocess.run(
            ["dpkg-query", "-W", "-f=${Package} ${Version} ${Status}\n",
             "nvidia-driver-*"],
            capture_output=True, text=True, timeout=4,
        )
    except (FileNotFoundError, subprocess.SubprocessError, OSError):
        return None
    if r.returncode != 0 or not r.stdout:
        return None
    installed: list = []
    for line in r.stdout.splitlines():
        parts = line.split(None, 2)
        if len(parts) < 3:
            continue
        pkg, ver, status = parts[0], parts[1], parts[2]
        if "install ok installed" not in status:
            continue
        installed.append({"package": pkg, "version": ver})
    if not installed:
        return None
    # Return the highest version
    installed.sort(key=lambda d: d["version"], reverse=True)
    return installed[0]


_RX_HISTORY_ENTRY = re.compile(
    r"^Start-Date:\s*(\S+\s+\S+).*?End-Date:\s*(\S+\s+\S+)",
    re.DOTALL,
)


def parse_apt_history(content: str) -> list:
    """Extract install/upgrade events touching nvidia-driver-* packages.
    Returns list of {start_ts_iso, action, packages: [{name, ver_from, ver_to}]}."""
    events: list = []
    # Entries are separated by blank lines
    chunks = re.split(r"\n\s*\n", content)
    for chunk in chunks:
        if "nvidia-driver" not in chunk:
            continue
        m_start = re.search(r"^Start-Date:\s*(.+)$", chunk, re.MULTILINE)
        m_install = re.search(r"^Install:\s*(.+)$", chunk, re.MULTILINE)
        m_upgrade = re.search(r"^Upgrade:\s*(.+)$", chunk, re.MULTILINE)
        m_remove = re.search(r"^Remove:\s*(.+)$", chunk, re.MULTILINE)
        line = m_install or m_upgrade or m_remove
        if not line:
            continue
        action = "install" if m_install else ("upgrade" if m_upgrade else "remove")
        body = line.group(1)
        pkgs: list = []
        # Each package : 'name:arch (oldver, newver)' or 'name:arch (ver)'
        for m in re.finditer(r"(nvidia-driver-[\w.-]+):\w+\s*\(([^)]+)\)", body):
            pkg = m.group(1)
            vers = [v.strip() for v in m.group(2).split(",")]
            ver_from = vers[0] if len(vers) >= 2 else None
            ver_to = vers[-1] if vers else None
            pkgs.append({"name": pkg, "ver_from": ver_from, "ver_to": ver_to})
        if not pkgs:
            continue
        events.append({
            "start": m_start.group(1).strip() if m_start else "",
            "action": action,
            "packages": pkgs,
        })
    return events


def read_apt_history() -> str:
    """Read /var/log/apt/history.log. Returns empty string if missing
    or not readable."""
    try:
        with open(_APT_HISTORY) as f:
            return f.read()
    except OSError:
        return ""


def find_cached_deb(package: str, version: str) -> Optional[str]:
    """Look in /var/cache/apt/archives for the .deb matching pkg+version."""
    import glob
    pattern = os.path.join(_APT_ARCHIVES, f"{package}_*.deb")
    for path in glob.glob(pattern):
        if version in os.path.basename(path):
            return path
    return None


def vault_copy(src_deb: str) -> Optional[str]:
    """Copy a .deb into the vault, return new path. None on failure."""
    if not os.path.isfile(src_deb):
        return None
    dst_dir = vault_dir()
    os.makedirs(dst_dir, exist_ok=True)
    dst = os.path.join(dst_dir, os.path.basename(src_deb))
    try:
        shutil.copy2(src_deb, dst)
        return dst
    except OSError:
        return None


def list_vault() -> list:
    """Return {name, path, size_bytes, ts} for each .deb in the vault."""
    d = vault_dir()
    if not os.path.isdir(d):
        return []
    out: list = []
    for name in os.listdir(d):
        if not name.endswith(".deb"):
            continue
        path = os.path.join(d, name)
        try:
            st = os.stat(path)
        except OSError:
            continue
        out.append({
            "name": name, "path": path,
            "size_bytes": st.st_size,
            "ts": int(st.st_mtime),
        })
    return sorted(out, key=lambda r: -r["ts"])


def prune_vault(max_files: int = _VAULT_MAX) -> int:
    """Keep only the `max_files` most-recent .deb files. Returns count
    pruned."""
    items = list_vault()
    pruned = 0
    for item in items[max_files:]:
        try:
            os.remove(item["path"])
            pruned += 1
        except OSError:
            pass
    return pruned


def stash_current_deb() -> dict:
    """Try to capture the currently-installed nvidia-driver .deb into
    the vault, if it's still in /var/cache/apt/archives."""
    cur = current_driver()
    if not cur:
        return {"ok": False, "reason": "no nvidia-driver-* installed"}
    src = find_cached_deb(cur["package"], cur["version"])
    if not src:
        return {"ok": False, "reason": f"no cached .deb for {cur['package']}_{cur['version']}",
                "current": cur}
    dst = vault_copy(src)
    if not dst:
        return {"ok": False, "reason": "vault copy failed"}
    prune_vault()
    return {"ok": True, "vaulted_path": dst, "current": cur}


def build_rollback_script(target_deb: str, current_pkg: str) -> str:
    """Emit a bash script the USER must run with sudo. We never run it
    automatically — too dangerous."""
    name = os.path.basename(target_deb)
    return f"""#!/usr/bin/env bash
# GPU driver rollback script — generated by gpu-dashboard
# YOU MUST REVIEW AND RUN THIS WITH SUDO.
# Target rollback : {name}
# Current package : {current_pkg}
set -euo pipefail

if [ "$(id -u)" -ne 0 ]; then
    echo "Run with sudo : sudo bash $0"
    exit 1
fi

echo "Rolling back to : {name}"
echo "  (you have 5 seconds to Ctrl-C if this looks wrong)"
sleep 5

# Hold the current package version to prevent immediate re-upgrade
apt-mark hold {current_pkg} || true

# Install the vaulted .deb (allows downgrades)
apt install --allow-downgrades -y "{target_deb}"

echo "  Done. You probably want to reboot to ensure the kernel module reloads cleanly."
"""


def status() -> dict:
    """Top-level vault state for the UI."""
    cur = current_driver()
    items = list_vault()
    history_content = read_apt_history()
    events = parse_apt_history(history_content)[-10:] if history_content else []
    return {
        "ok": True,
        "vault_dir": vault_dir(),
        "current": cur,
        "vaulted": items,
        "vault_max": _VAULT_MAX,
        "recent_events": events,
    }
