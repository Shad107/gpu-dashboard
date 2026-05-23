"""Module pstore_crashlog_audit — persistent crash-log inventory
(R&D #68.1).

`pstore` is the Linux interface for persistent storage of kernel
panic / oops / dmesg-fragments across reboots, backed by one of
several mechanisms :
  * **ramoops** — pre-reserved RAM region (most reliable on bare
                    metal).
  * **efi_pstore** — UEFI variables (works on most desktops + VMs
                    when EFI variables are writable).
  * **erst** — ACPI Error Record Serialization Table.
  * **mtd / blk / zone** — flash / block-device storage.

When a kernel panic is captured into pstore, the next boot leaves
a `dmesg-<backend>-<id>` file under `/sys/fs/pstore/`. Owners of
homelab boxes routinely fail to notice these because nothing in
the desktop UI calls them out — the audit closes that gap.

Reads :
  /proc/mounts                             (filter pstore lines)
  /sys/module/pstore/parameters/backend    active backend name
  /sys/module/efi_pstore/parameters/*      backend-specific knobs
  /sys/fs/pstore/                          file inventory (root)

Verdicts (priority order) :
  stale_panic_logs_present  ≥1 readable dmesg-* or panic-* entry
                              under /sys/fs/pstore/.
  pstore_backend_absent     pstore *is* mounted but
                              /sys/module/pstore/parameters/backend
                              reads "(null)" or is unset — no
                              backend will ever write.
  requires_root             /sys/fs/pstore exists but unreadable
                              (typical desktop case).
  ok                        pstore mounted + backend live + no
                              stale entries.
  unknown                   pstore filesystem not mounted (kernel
                              built without CONFIG_PSTORE or never
                              configured).

stdlib only.
"""
from __future__ import annotations

import os
from typing import List, Optional


NAME = "pstore_crashlog_audit"


_PROC_MOUNTS = "/proc/mounts"
_SYS_PSTORE = "/sys/fs/pstore"
_SYS_PSTORE_BACKEND = "/sys/module/pstore/parameters/backend"

_PANIC_LIKE_PREFIXES = ("dmesg-", "panic-", "oops-", "fault-",
                           "console-", "ftrace-", "pmsg-")


def _read(path: str) -> Optional[str]:
    try:
        with open(path) as f:
            return f.read()
    except OSError:
        return None


def is_pstore_mounted(proc_mounts: str = _PROC_MOUNTS) -> bool:
    text = _read(proc_mounts)
    if text is None:
        return False
    for ln in text.splitlines():
        parts = ln.split()
        if len(parts) >= 3 and parts[2] == "pstore":
            return True
    return False


def read_backend(path: str = _SYS_PSTORE_BACKEND) -> Optional[str]:
    txt = _read(path)
    if txt is None:
        return None
    txt = txt.strip()
    if not txt or txt == "(null)":
        return None
    return txt


def list_pstore_entries(sys_pstore: str = _SYS_PSTORE) -> dict:
    """Returns {present, eacces, files:list[dict]}."""
    out = {"present": False, "eacces": False, "files": []}
    if not os.path.isdir(sys_pstore):
        return out
    out["present"] = True
    try:
        names = sorted(os.listdir(sys_pstore))
    except PermissionError:
        out["eacces"] = True
        return out
    except OSError:
        out["eacces"] = True
        return out
    files: List[dict] = []
    for n in names:
        if not any(n.startswith(p)
                      for p in _PANIC_LIKE_PREFIXES):
            continue
        full = os.path.join(sys_pstore, n)
        try:
            size = os.path.getsize(full)
        except OSError:
            size = None
        files.append({"name": n, "size": size})
    out["files"] = files
    return out


def classify(mounted: bool, backend: Optional[str],
              entries: dict) -> dict:
    if not mounted:
        return {"verdict": "unknown",
                "reason": ("pstore is not mounted — kernel built "
                          "without CONFIG_PSTORE or never set up. "
                          "Crashes cannot be persisted."),
                "recommendation": _recipe_not_mounted()}

    # 1) stale_panic_logs_present — visible files
    if entries.get("files"):
        names = ", ".join(e["name"] for e in entries["files"][:3])
        return {"verdict": "stale_panic_logs_present",
                "reason": (f"{len(entries['files'])} persistent "
                          f"crash-log entry/entries in "
                          f"/sys/fs/pstore : {names}."),
                "recommendation": _recipe_stale_panic_logs()}

    # 2) pstore_backend_absent
    if not backend:
        return {"verdict": "pstore_backend_absent",
                "reason": ("pstore is mounted but no backend is "
                          "registered (/sys/module/pstore/"
                          "parameters/backend is unset). "
                          "Crashes will not be captured."),
                "recommendation": _recipe_no_backend()}

    # 3) requires_root — dir present but listing denied
    if entries.get("eacces"):
        return {"verdict": "requires_root",
                "reason": ("/sys/fs/pstore is root-only — running "
                          "as an unprivileged user so we cannot "
                          "enumerate stale crash logs (backend = "
                          f"{backend})."),
                "recommendation": _recipe_requires_root()}

    return {"verdict": "ok",
            "reason": (f"pstore mounted with backend = {backend} ; "
                      f"no stale crash-log entries."),
            "recommendation": ""}


def status(config=None,
            proc_mounts: str = _PROC_MOUNTS,
            sys_pstore: str = _SYS_PSTORE,
            sys_backend: str = _SYS_PSTORE_BACKEND) -> dict:
    mounted = is_pstore_mounted(proc_mounts)
    backend = read_backend(sys_backend)
    entries = list_pstore_entries(sys_pstore)
    verdict = classify(mounted, backend, entries)
    return {"ok": mounted,
              "mounted": mounted,
              "backend": backend,
              "directory_present": entries["present"],
              "permission_denied": entries["eacces"],
              "entry_count": len(entries["files"]),
              "entries": entries["files"][:10],
              "verdict": verdict}


# ── recovery recipes ────────────────────────────────────────────

def _recipe_not_mounted() -> str:
    return ("# Enable pstore in fstab :\n"
            "echo 'pstore /sys/fs/pstore pstore defaults 0 0' \\\n"
            "  | sudo tee -a /etc/fstab\n"
            "sudo mount -a\n"
            "# Confirm a backend is available :\n"
            "ls /sys/module | grep -E 'ramoops|efi_pstore|erst'\n")


def _recipe_stale_panic_logs() -> str:
    return ("# Inspect persistent crash logs :\n"
            "sudo ls -l /sys/fs/pstore/\n"
            "sudo cat /sys/fs/pstore/dmesg-*\n"
            "# Capture a copy and clear pstore :\n"
            "sudo cp /sys/fs/pstore/* /var/log/pstore-archive/\n"
            "sudo rm /sys/fs/pstore/dmesg-*\n")


def _recipe_no_backend() -> str:
    return ("# pstore mounted but no backend. Modprobe a backend :\n"
            "# Most common on a desktop/VM :\n"
            "sudo modprobe efi_pstore\n"
            "# For ramoops with reserved RAM :\n"
            "sudo modprobe ramoops mem_address=0x... mem_size=...\n"
            "# Check after :\n"
            "cat /sys/module/pstore/parameters/backend\n")


def _recipe_requires_root() -> str:
    return ("# /sys/fs/pstore is 0750 root:root. Inspect via :\n"
            "sudo ls -l /sys/fs/pstore/\n"
            "# Optional : grant your group read access via udev,\n"
            "# but be careful — these files contain kernel debug\n"
            "# data that may reveal addresses (KASLR-sensitive).\n")
