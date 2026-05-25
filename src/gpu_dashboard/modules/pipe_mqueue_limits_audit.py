"""Module pipe_mqueue_limits_audit — pipe / POSIX-mqueue /
epoll user-resource limits (R&D #93.1).

Three modules touch related fs sysctls but none read these :

  * vfs_limits_audit       — file-nr / aio / nr_open only
  * inotify_audit          — /proc/sys/fs/inotify/*
  * unix_socket_inventory — /proc/net/unix (not POSIX mqueue)

This audit owns the three remaining per-user IPC ceilings :

  /proc/sys/fs/pipe-max-size                pipe buffer size cap
  /proc/sys/fs/pipe-user-pages-soft         soft cap on pages
  /proc/sys/fs/pipe-user-pages-hard         hard cap on pages
  /proc/sys/fs/mqueue/queues_max            POSIX mqueue count
  /proc/sys/fs/mqueue/msg_max               per-queue msg cap
  /proc/sys/fs/mqueue/msgsize_max           per-msg byte cap
  /proc/sys/fs/epoll/max_user_watches       epoll fd watches
                                            per UID
  /proc/meminfo                             MemTotal cross-ref

Verdicts (worst-first) :

  mqueue_exhausted        err   queues_max ≤ 0 (POSIX mqueue
                                effectively disabled) or
                                msgsize_max < 8192 (way below
                                kernel default 8192 / hardened
                                preset).
  pipe_user_pages_low     warn  pipe-user-pages-soft > 0 and
                                < 4096 — `splice()` / pipes
                                from CI agents will EBUSY
                                under load.
  epoll_watches_low       warn  max_user_watches < 1 048 576
                                on a ≥ 16 GiB box — IDE
                                running on a large monorepo
                                will hit the wall.
  non_default_pipe_max    accent pipe-max-size != 1 048 576
                                 (kernel default) — quietly
                                 changed from default.
  ok                      all knobs at safe values.
  unknown                 /proc/sys/fs/pipe-max-size absent.

stdlib only.
"""
from __future__ import annotations

import os
import re
from typing import Optional

NAME = "pipe_mqueue_limits_audit"

DEFAULT_PROC_SYS_FS = "/proc/sys/fs"
DEFAULT_PROC_MEMINFO = "/proc/meminfo"

# Thresholds.
_PIPE_PAGES_LOW = 4096
_EPOLL_WATCHES_LOW = 1_048_576
_BIG_BOX_BYTES = 16 * 2**30
_MSGSIZE_MAX_LOW = 8192
_PIPE_MAX_SIZE_DEFAULT = 1_048_576

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
        "pipe_max_size": _read_int(
            os.path.join(root, "pipe-max-size")),
        "pipe_user_pages_soft": _read_int(
            os.path.join(root, "pipe-user-pages-soft")),
        "pipe_user_pages_hard": _read_int(
            os.path.join(root, "pipe-user-pages-hard")),
        "queues_max": _read_int(
            os.path.join(root, "mqueue", "queues_max")),
        "msg_max": _read_int(
            os.path.join(root, "mqueue", "msg_max")),
        "msgsize_max": _read_int(
            os.path.join(root, "mqueue", "msgsize_max")),
        "epoll_max_user_watches": _read_int(
            os.path.join(
                root, "epoll", "max_user_watches")),
    }


def classify(limits: dict,
             mem_total: Optional[int]) -> dict:
    if limits["pipe_max_size"] is None:
        return {"verdict": "unknown",
                "reason": (
                    "/proc/sys/fs/pipe-max-size absent — "
                    "procfs unavailable.")}

    # err — mqueue effectively unusable
    qmax = limits["queues_max"]
    msgsize_max = limits["msgsize_max"]
    if qmax is not None and qmax <= 0:
        return {"verdict": "mqueue_exhausted",
                "reason": (
                    f"fs.mqueue.queues_max = {qmax} — POSIX "
                    "mqueue effectively disabled. Any "
                    "mq_open() call will fail.")}
    if msgsize_max is not None and msgsize_max < _MSGSIZE_MAX_LOW:
        return {"verdict": "mqueue_exhausted",
                "reason": (
                    f"fs.mqueue.msgsize_max = "
                    f"{msgsize_max} (< {_MSGSIZE_MAX_LOW}) — "
                    "POSIX messages capped tiny. Common in "
                    "hardened distros.")}

    # warn — pipe user pages too low
    pp_soft = limits["pipe_user_pages_soft"]
    if pp_soft is not None and 0 < pp_soft < _PIPE_PAGES_LOW:
        return {"verdict": "pipe_user_pages_low",
                "reason": (
                    f"fs.pipe-user-pages-soft = {pp_soft} "
                    f"(< {_PIPE_PAGES_LOW}) — CI agents / "
                    "splice() consumers will EBUSY under "
                    "concurrent pipe load.")}

    # warn — epoll watches low on big-RAM box
    ep = limits["epoll_max_user_watches"]
    if (ep is not None and ep < _EPOLL_WATCHES_LOW
            and mem_total is not None
            and mem_total >= _BIG_BOX_BYTES):
        return {"verdict": "epoll_watches_low",
                "reason": (
                    f"fs.epoll.max_user_watches = {ep} "
                    f"(< {_EPOLL_WATCHES_LOW}) on a "
                    f"{mem_total / 2**30:.0f} GiB box — IDE "
                    "running on a large monorepo will hit "
                    "the wall.")}

    # accent — pipe-max-size != distro default
    pms = limits["pipe_max_size"]
    if pms is not None and pms != _PIPE_MAX_SIZE_DEFAULT:
        return {"verdict": "non_default_pipe_max",
                "reason": (
                    f"fs.pipe-max-size = {pms} (default "
                    f"{_PIPE_MAX_SIZE_DEFAULT}) — quietly "
                    "changed from default ; trace which "
                    "sysctl drop-in set it.")}

    return {"verdict": "ok",
            "reason": (
                f"pipe-max-size={pms}, mqueue queues_max="
                f"{qmax}, epoll watches={ep} — "
                "limits coherent.")}


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
