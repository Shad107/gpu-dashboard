"""Module xfs_log_activity_audit — XFS log + stats activity
surface (R&D #110.2).

fs_specific_tunables_audit scans /sys/fs/xfs/<dev>/stats/stats
but only runs a corruption regex (line 135). The log-activity
counters and the broader stats blob are otherwise unparsed.

XFS stats blob format (whitespace-separated, per-category):
  extent_alloc <count> <bytes> ...
  abt <count> ...
  ...
  xstrat <count> <bytes>
  rw <reads> <writes>
  attr <count> ...
  ...

We surface two informational signals on hosts with XFS :

  - filesystem count
  - read/write counter from the `rw` row

Acknowledged: no live XFS on this audit's reference VM, so the
verdict is `unknown` here. Real value is on homelab NAS / Steam
library boxes with XFS.

Reads :

  /sys/fs/xfs/                directory enumeration
  /sys/fs/xfs/<dev>/stats/stats

Verdicts (worst-first) :

  xfs_stats_unreadable        accent  XFS filesystems exist
                                      but no stats file
                                      readable.
  ok                                  XFS present, stats
                                      coherent, OR XFS absent.
  requires_root                       /sys/fs/xfs unreadable.
  unknown                             /sys/fs/xfs absent
                                      (no XFS mounted).

stdlib only.
"""
from __future__ import annotations

import os
from typing import Optional

NAME = "xfs_log_activity_audit"

DEFAULT_SYS_XFS = "/sys/fs/xfs"


def _read_text(path: str) -> Optional[str]:
    try:
        with open(path, "r", encoding="utf-8") as fh:
            return fh.read()
    except (OSError, PermissionError):
        return None


def parse_rw_row(text: Optional[str]) -> Optional[dict]:
    """Find 'rw <reads> <writes>' row in stats blob."""
    if not text:
        return None
    for line in text.splitlines():
        parts = line.split()
        if len(parts) >= 3 and parts[0] == "rw":
            try:
                return {"reads": int(parts[1]),
                        "writes": int(parts[2])}
            except ValueError:
                continue
    return None


def walk_xfs(sys_xfs: str = DEFAULT_SYS_XFS) -> list:
    """Return list of {dev, rw} per XFS filesystem."""
    out: list = []
    if not os.path.isdir(sys_xfs):
        return out
    try:
        entries = sorted(os.listdir(sys_xfs))
    except OSError:
        return out
    for ent in entries:
        stats_path = os.path.join(
            sys_xfs, ent, "stats", "stats")
        if not os.path.isfile(stats_path):
            continue
        rw = parse_rw_row(_read_text(stats_path))
        out.append({"dev": ent, "rw": rw})
    return out


def classify(sys_xfs_present: bool,
             sys_xfs_readable: bool,
             filesystems: list) -> dict:
    if not sys_xfs_present:
        return {"verdict": "unknown",
                "reason": (
                    "/sys/fs/xfs absent — no XFS "
                    "filesystem mounted.")}
    if not sys_xfs_readable:
        return {"verdict": "requires_root",
                "reason": (
                    "/sys/fs/xfs unreadable — re-run as "
                    "root.")}
    if not filesystems:
        return {"verdict": "unknown",
                "reason": (
                    "/sys/fs/xfs exists but no readable "
                    "filesystem entries.")}

    # accent — any filesystem has no rw data
    unreadable = [f for f in filesystems
                  if f["rw"] is None]
    if len(unreadable) == len(filesystems):
        return {
            "verdict": "xfs_stats_unreadable",
            "reason": (
                f"{len(filesystems)} XFS filesystem(s) "
                "exist but stats/stats unreadable on all of "
                "them — re-run as root for log activity.")}

    return {"verdict": "ok",
            "reason": (
                f"{len(filesystems)} XFS filesystem(s) "
                "with readable stats.")}


def status(config: Optional[dict] = None,
           sys_xfs: str = DEFAULT_SYS_XFS) -> dict:
    sys_xfs_present = os.path.isdir(sys_xfs)
    sys_xfs_readable = (
        sys_xfs_present and os.access(sys_xfs, os.R_OK))
    filesystems = (walk_xfs(sys_xfs)
                   if sys_xfs_readable else [])
    verdict = classify(sys_xfs_present, sys_xfs_readable,
                       filesystems)
    return {
        "ok": verdict["verdict"] == "ok",
        "filesystem_count": len(filesystems),
        "filesystems": [
            {"dev": f["dev"],
             "rw_reads": (
                 f["rw"]["reads"] if f["rw"] else None),
             "rw_writes": (
                 f["rw"]["writes"] if f["rw"] else None)}
            for f in filesystems],
        "verdict": verdict,
    }
