"""Module loop_device_audit — kernel loop-device backing
audit (R&D #84.2).

Snap, flatpak and containerd leak loop devices over time.
Phantom loops pinned to deleted backing files are the
classic "disk space won't free" mystery on homelab boxes —
``df`` shows the partition full, ``du`` doesn't find the
data, and the offender is a loop holding an unlinked file
open.

Reads, per /sys/block/loop<N>/ :

  size                     blocks (512-byte sectors)
  ro                       read-only flag
  loop/backing_file        path the kernel mapped ;
                           string ends with " (deleted)"
                           if the backing file has been
                           unlinked.

Plus :
  /sys/module/loop/parameters/max_loop  preallocated count

Verdicts (worst first) :

  loop_deleted_backing      ≥1 active loop's backing_file
                            ends with "(deleted)" — kernel
                            holds an unlinked inode open,
                            disk space is pinned.
  loop_unstable_backing     ≥1 backing file is under /tmp/
                            /dev/shm/ /run/ — disappears on
                            reboot, leaving the loop dead.
  excessive_loops           > 8 active loops attached
                            (snap / flatpak churn).
  ok                        loop devices sane or none in
                            use.
  n/a                       loop kernel module not loaded.
"""
from __future__ import annotations

import os
import re
from typing import Optional

DEFAULT_BLOCK_ROOT = "/sys/block"
DEFAULT_LOOP_MODULE = "/sys/module/loop"

# Unstable filesystem mount-point prefixes (volatile, lost
# on reboot).
_UNSTABLE_PREFIXES = ("/tmp/", "/dev/shm/", "/run/user/",
                       "/run/lock/")

# Threshold
_EXCESSIVE_LOOPS = 8


def _read_text(path: str) -> Optional[str]:
    try:
        with open(path, "r", encoding="utf-8") as fh:
            return fh.read().strip()
    except (OSError, PermissionError):
        return None


def _read_int(path: str) -> Optional[int]:
    s = _read_text(path)
    if s is None or s == "":
        return None
    try:
        return int(s)
    except ValueError:
        return None


def is_loop_module_loaded(
        module_root: str = DEFAULT_LOOP_MODULE) -> bool:
    return os.path.isdir(module_root)


def list_loops(root: str = DEFAULT_BLOCK_ROOT) -> list[str]:
    try:
        entries = sorted(os.listdir(root))
    except OSError:
        return []
    return [
        e for e in entries
        if re.match(r"^loop\d+$", e)]


def read_loop(root: str, name: str) -> dict:
    """Returns per-loop info incl. backing_file (None if
    inactive)."""
    d = os.path.join(root, name)
    backing = _read_text(
        os.path.join(d, "loop", "backing_file"))
    return {
        "name": name,
        "size_sectors": _read_int(os.path.join(d, "size")),
        "ro": _read_int(os.path.join(d, "ro")),
        "backing_file": backing,
    }


def _is_deleted(backing: Optional[str]) -> bool:
    return bool(backing) and backing.endswith("(deleted)")


def _is_unstable(backing: Optional[str]) -> bool:
    if not backing:
        return False
    # Strip "(deleted)" suffix for path check
    path = backing
    if path.endswith("(deleted)"):
        path = path[:-len("(deleted)")].strip()
    return any(path.startswith(p) for p in _UNSTABLE_PREFIXES)


def classify(loops: list[dict],
             module_loaded: bool) -> dict:
    if not module_loaded:
        return {"verdict": "n/a",
                "reason": (
                    "loop kernel module not loaded ; "
                    "/sys/module/loop absent.")}

    active = [
        l for l in loops
        if l["backing_file"] and l["size_sectors"]]

    # 1. err — deleted backing
    deleted = [
        l for l in active
        if _is_deleted(l["backing_file"])]
    if deleted:
        first = deleted[0]
        return {"verdict": "loop_deleted_backing",
                "reason": (
                    f"{len(deleted)} loop(s) holding "
                    f"deleted backing file(s) (first: "
                    f"{first['name']} → "
                    f"{first['backing_file']}). Disk space "
                    "is pinned by unlinked inodes."),
                "deleted_count": len(deleted),
                "first": first["name"]}

    # 2. warn — unstable backing
    unstable = [
        l for l in active
        if _is_unstable(l["backing_file"])]
    if unstable:
        first = unstable[0]
        return {"verdict": "loop_unstable_backing",
                "reason": (
                    f"{len(unstable)} loop(s) backed by "
                    f"volatile path(s) (first: "
                    f"{first['name']} → "
                    f"{first['backing_file']}). Backing "
                    "vanishes on reboot."),
                "unstable_count": len(unstable),
                "first": first["name"]}

    # 3. accent — excessive count
    if len(active) > _EXCESSIVE_LOOPS:
        return {"verdict": "excessive_loops",
                "reason": (
                    f"{len(active)} active loop devices — "
                    "likely snap / flatpak / container "
                    "churn."),
                "active_count": len(active)}

    return {"verdict": "ok",
            "reason": (
                f"{len(active)} active loop(s), all backing "
                "files on stable paths.")}


def status(config: Optional[dict] = None,
           root: str = DEFAULT_BLOCK_ROOT,
           module_root: str = DEFAULT_LOOP_MODULE) -> dict:
    module_loaded = is_loop_module_loaded(module_root)
    loops = ([read_loop(root, n) for n in list_loops(root)]
              if module_loaded else [])
    verdict = classify(loops, module_loaded)
    active = [l for l in loops if l["backing_file"]]
    return {
        "ok": verdict["verdict"] not in (
            "loop_deleted_backing",),
        "loop_count_total": len(loops),
        "loop_count_active": len(active),
        "loops": [
            {"name": l["name"],
             "size_sectors": l["size_sectors"],
             "ro": l["ro"],
             "backing_file": l["backing_file"]}
            for l in active],
        "verdict": verdict,
    }
