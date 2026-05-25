"""Module fs_aio_fanotify_limits_audit — fanotify caps +
aio-max-nr "still default" detector (R&D #94.2).

Three modules touch related fs sysctls but none read these :

  * vfs_limits_audit       — file-max / nr_open + aio-nr
                             utilization (aio_nr_high warn).
  * inotify_audit          — /proc/sys/fs/inotify/* only.
  * pipe_mqueue_limits_audit (#93.1) — pipe / POSIX-mqueue
                             / epoll caps.

Neither reads fanotify caps, and vfs_limits_audit's aio
verdict is utilization-based (aio_nr / aio_max ratio).
This audit fires on the orthogonal "aio-max-nr is still at
the 64 K kernel default on a GPU/io_uring/Docker box"
signal — independent of current utilization.

Reads :

  /proc/sys/fs/aio-max-nr                    cap (default
                                             65 536)
  /proc/sys/fs/fanotify/max_queued_events
  /proc/sys/fs/fanotify/max_user_groups
  /proc/sys/fs/fanotify/max_user_marks       per-uid mark cap
  /proc/meminfo                              MemTotal cross-ref

Verdicts (worst-first) :

  fanotify_marks_low      warn  max_user_marks < 16 384 on
                                a ≥ 16 GiB box — file-system
                                watchers will EBUSY under
                                container / antivirus load.
  aio_max_default_low     accent aio-max-nr ≤ 65 536 (kernel
                                default) — explicit "still
                                at default" signal,
                                independent of current util.
                                io_uring / MinIO / Docker-
                                storage can chew through 64 K
                                contexts fast.
  ok                      caps look bumped or wide enough.
  unknown                 /proc/sys/fs absent.

stdlib only.
"""
from __future__ import annotations

import os
import re
from typing import Optional

NAME = "fs_aio_fanotify_limits_audit"

DEFAULT_PROC_SYS_FS = "/proc/sys/fs"
DEFAULT_PROC_MEMINFO = "/proc/meminfo"

# Thresholds.
_MARKS_LOW_THRESHOLD = 16384
_BIG_BOX_BYTES = 16 * 2**30
_AIO_MAX_DEFAULT = 65536

_MEMTOTAL_RE = re.compile(r"^MemTotal:\s*(\d+)\s*kB",
                          re.MULTILINE)


def _read_int(path: str) -> Optional[int]:
    try:
        with open(path, "r", encoding="utf-8") as fh:
            return int(fh.read().strip())
    except (OSError, PermissionError, ValueError):
        return None


def _read_text(path: str) -> Optional[str]:
    try:
        with open(path, "r", encoding="utf-8") as fh:
            return fh.read()
    except (OSError, PermissionError):
        return None


def parse_meminfo_total_bytes(text: str) -> Optional[int]:
    if not text:
        return None
    m = _MEMTOTAL_RE.search(text)
    if not m:
        return None
    try:
        return int(m.group(1)) * 1024
    except ValueError:
        return None


def read_limits(root: str = DEFAULT_PROC_SYS_FS) -> dict:
    return {
        "aio_max_nr": _read_int(
            os.path.join(root, "aio-max-nr")),
        "max_queued_events": _read_int(
            os.path.join(
                root, "fanotify", "max_queued_events")),
        "max_user_groups": _read_int(
            os.path.join(
                root, "fanotify", "max_user_groups")),
        "max_user_marks": _read_int(
            os.path.join(
                root, "fanotify", "max_user_marks")),
    }


def classify(limits: dict,
             mem_total: Optional[int]) -> dict:
    aio_max = limits.get("aio_max_nr")
    marks = limits.get("max_user_marks")

    if aio_max is None and marks is None:
        return {"verdict": "unknown",
                "reason": (
                    "/proc/sys/fs/{aio-max-nr,fanotify/*} "
                    "absent — procfs unavailable.")}

    # warn — fanotify marks low on big-RAM host
    if (marks is not None and marks < _MARKS_LOW_THRESHOLD
            and mem_total is not None
            and mem_total >= _BIG_BOX_BYTES):
        return {
            "verdict": "fanotify_marks_low",
            "reason": (
                f"fs.fanotify.max_user_marks = {marks} "
                f"(< {_MARKS_LOW_THRESHOLD}) on a "
                f"{mem_total / 2**30:.0f} GiB box — "
                "file-system watchers (antivirus, container "
                "engines) will EBUSY under load.")}

    # accent — aio-max-nr at kernel default
    if aio_max is not None and aio_max <= _AIO_MAX_DEFAULT:
        return {
            "verdict": "aio_max_default_low",
            "reason": (
                f"fs.aio-max-nr = {aio_max} (still at kernel "
                f"default {_AIO_MAX_DEFAULT}) — io_uring / "
                "MinIO / Docker-storage workloads can chew "
                "through 64 K contexts. Bump on rigs that "
                "run any of those.")}

    return {"verdict": "ok",
            "reason": (
                f"aio-max-nr = {aio_max} ; "
                f"fanotify.max_user_marks = {marks} — "
                "caps coherent for a GPU homelab.")}


def status(config: Optional[dict] = None,
           proc_sys_fs: str = DEFAULT_PROC_SYS_FS,
           proc_meminfo: str = DEFAULT_PROC_MEMINFO) -> dict:
    limits = read_limits(proc_sys_fs)
    mem_total = parse_meminfo_total_bytes(
        _read_text(proc_meminfo) or "")
    verdict = classify(limits, mem_total)
    return {
        "ok": verdict["verdict"] == "ok",
        "limits": limits,
        "mem_total": mem_total,
        "verdict": verdict,
    }
