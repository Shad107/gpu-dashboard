"""Module block_holders_stack_audit — dm/md stack topology +
suspend/degraded/orphan detector (R&D #96.4).

Existing modules touch nearby surface but never the holders
graph + dm/md attribute correlation :

  * mdraid_health      — /proc/mdstat summary
  * disk_health        — SMART attrs
  * block_queue_audit  — scheduler / wbt
  * trim_audit         — TRIM / fstrim
  * nvme_* modules     — NVMe-specific

This audit walks /sys/block/* for dm-*/md* devices, reads
their state attributes, and cross-references /proc/mounts +
/proc/swaps to find :

  * dm devices SUSPENDED while still backing a live FS or
    swap area — I/O is frozen, processes will hang.
  * md devices DEGRADED with sync_action=idle — no resync
    in flight.
  * dm devices that exist but have no holders, no mount,
    no swap — leftover from a torn-down stack, still
    pinning LVM extents.

Reads :

  /sys/block/<name>/holders/
  /sys/block/dm-*/dm/{name, suspended}
  /sys/block/md*/md/{degraded, sync_action, array_state}
  /proc/mounts
  /proc/swaps

Verdicts (worst-first) :

  dm_suspended_with_mount    err   dm device with
                                   dm/suspended=1 AND it's a
                                   mount or swap source.
  md_degraded_no_resync      warn  any md array with
                                   degraded ≥ 1 AND
                                   sync_action=idle.
  orphan_dm_device           accent dm device with no
                                   holders, not in mounts /
                                   swaps — torn-down stack
                                   leftover.
  block_stack_sane           ok
  requires_root              dm attrs mode-600.
  unknown                    /sys/block absent.

stdlib only.
"""
from __future__ import annotations

import os
from typing import Optional

NAME = "block_holders_stack_audit"

DEFAULT_SYS_BLOCK = "/sys/block"
DEFAULT_PROC_MOUNTS = "/proc/mounts"
DEFAULT_PROC_SWAPS = "/proc/swaps"


def _read_text(path: str) -> Optional[str]:
    try:
        with open(path, "r", encoding="utf-8") as fh:
            return fh.read().strip()
    except (OSError, PermissionError):
        return None


def _read_int(path: str) -> Optional[int]:
    t = _read_text(path)
    if t is None:
        return None
    try:
        return int(t)
    except ValueError:
        return None


def parse_proc_mounts(text: str) -> set:
    """Return set of device-source strings appearing in
    /proc/mounts (e.g. '/dev/dm-1', '/dev/mapper/vg-lv',
    '/dev/md0')."""
    out: set = set()
    if not text:
        return out
    for line in text.splitlines():
        parts = line.split()
        if not parts:
            continue
        src = parts[0]
        if src.startswith("/dev/"):
            out.add(src)
    return out


def parse_proc_swaps(text: str) -> set:
    """Return set of /dev/* filenames from /proc/swaps."""
    out: set = set()
    if not text:
        return out
    for line in text.splitlines()[1:]:
        parts = line.split()
        if not parts:
            continue
        if parts[0].startswith("/dev/"):
            out.add(parts[0])
    return out


def _device_aliases(name: str, dm_name: str) -> list:
    """Return possible /dev/ paths for a block device name.
    For dm devices, both /dev/dm-N and /dev/mapper/<name>
    may appear in /proc/mounts."""
    out = [f"/dev/{name}"]
    if dm_name:
        out.append(f"/dev/mapper/{dm_name}")
    return out


def list_holders(base: str) -> list:
    h = os.path.join(base, "holders")
    if not os.path.isdir(h):
        return []
    try:
        return sorted(os.listdir(h))
    except OSError:
        return []


def walk_dm_md(root: str = DEFAULT_SYS_BLOCK) -> dict:
    """Walk /sys/block, return {'dm': [{...}], 'md': [{...}]}"""
    out: dict = {"dm": [], "md": []}
    if not os.path.isdir(root):
        return out
    try:
        names = sorted(os.listdir(root))
    except OSError:
        return out
    for name in names:
        base = os.path.join(root, name)
        if name.startswith("dm-"):
            dm_dir = os.path.join(base, "dm")
            dm_name = _read_text(
                os.path.join(dm_dir, "name")) or ""
            suspended = _read_int(
                os.path.join(dm_dir, "suspended"))
            holders = list_holders(base)
            out["dm"].append({
                "name": name,
                "dm_name": dm_name,
                "suspended": suspended,
                "holders": holders,
            })
        elif name.startswith("md"):
            md_dir = os.path.join(base, "md")
            degraded = _read_int(
                os.path.join(md_dir, "degraded"))
            sync_action = _read_text(
                os.path.join(md_dir, "sync_action")) or ""
            array_state = _read_text(
                os.path.join(md_dir, "array_state")) or ""
            out["md"].append({
                "name": name,
                "degraded": degraded,
                "sync_action": sync_action,
                "array_state": array_state,
            })
    return out


def classify(stack: dict, mounts: set, swaps: set,
             root_present: bool) -> dict:
    if not root_present:
        return {"verdict": "unknown",
                "reason": (
                    "/sys/block absent — no block device "
                    "tree to walk.")}

    dms = stack["dm"]
    mds = stack["md"]

    # err — dm suspended AND mounted
    suspended_mounted: list = []
    for d in dms:
        if d.get("suspended") != 1:
            continue
        for alias in _device_aliases(d["name"], d["dm_name"]):
            if alias in mounts or alias in swaps:
                suspended_mounted.append(d)
                break
    if suspended_mounted:
        names = [
            d["dm_name"] or d["name"]
            for d in suspended_mounted]
        return {
            "verdict": "dm_suspended_with_mount",
            "reason": (
                f"{len(suspended_mounted)} dm device(s) "
                f"SUSPENDED while still mounted/swapping: "
                f"{names}. I/O is frozen — processes "
                "writing will hang. Resume: dmsetup "
                "resume <name>.")}

    # warn — md degraded with no resync running
    degraded_idle = [
        m for m in mds
        if (m.get("degraded") or 0) > 0
        and m.get("sync_action") == "idle"]
    if degraded_idle:
        names = [m["name"] for m in degraded_idle]
        return {
            "verdict": "md_degraded_no_resync",
            "reason": (
                f"{len(degraded_idle)} md array(s) DEGRADED "
                f"with sync_action=idle: {names}. Rebuild "
                "is NOT in progress — add a spare or kick "
                "with: echo repair > /sys/block/<md>/md/"
                "sync_action.")}

    # accent — orphan dm (no holders, not in mounts/swaps,
    # not suspended)
    orphans: list = []
    for d in dms:
        if d.get("suspended") == 1:
            continue
        if d["holders"]:
            continue
        if any(a in mounts or a in swaps
               for a in _device_aliases(
                   d["name"], d["dm_name"])):
            continue
        orphans.append(d)
    if orphans:
        names = [
            o["dm_name"] or o["name"] for o in orphans]
        return {
            "verdict": "orphan_dm_device",
            "reason": (
                f"{len(orphans)} dm device(s) have no "
                f"holders and aren't in /proc/mounts or "
                f"/proc/swaps: {names}. Stale stack remnants "
                "pinning LVM PEs. Clean up: "
                "dmsetup remove <name>.")}

    return {"verdict": "block_stack_sane",
            "reason": (
                f"{len(dms)} dm device(s), {len(mds)} md "
                "array(s) inspected ; no suspended-mounted, "
                "no degraded-idle, no orphans.")}


def status(config: Optional[dict] = None,
           sys_block: str = DEFAULT_SYS_BLOCK,
           proc_mounts: str = DEFAULT_PROC_MOUNTS,
           proc_swaps: str = DEFAULT_PROC_SWAPS) -> dict:
    root_present = os.path.isdir(sys_block)
    stack = walk_dm_md(sys_block) if root_present else {
        "dm": [], "md": []}
    mounts = parse_proc_mounts(
        _read_text(proc_mounts) or "")
    swaps = parse_proc_swaps(
        _read_text(proc_swaps) or "")
    verdict = classify(stack, mounts, swaps, root_present)
    return {
        "ok": verdict["verdict"] == "block_stack_sane",
        "dm_count": len(stack["dm"]),
        "md_count": len(stack["md"]),
        "verdict": verdict,
    }
