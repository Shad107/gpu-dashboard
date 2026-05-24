"""Module sysvipc_limits_audit — SysV IPC kernel.shm* limits +
RAM cross-ref (R&D #89.3).

sysvipc_audit (existing module) reads the *contents* of
/proc/sysvipc/{shm,sem,msg} — orphan segments, stale queues.
It does NOT read the kernel.* sysctl LIMITS that determine
whether new segments can be created at all. This audit owns
that orthogonal axis.

Reads :

  /proc/sys/kernel/shmmax   max bytes per SHM segment
  /proc/sys/kernel/shmall   max total pages of SHM
  /proc/sys/kernel/shmmni   max number of SHM segments
  /proc/sys/kernel/msgmni   max number of msg queues
  /proc/meminfo             MemTotal cross-ref

  PAGE_SIZE via os.sysconf("SC_PAGE_SIZE").

Verdicts (worst-first) :

  shmmax_zero          err   shmmax = 0 — SysV SHM
                             effectively disabled, CUDA-MPS /
                             PostgreSQL / X11 IPC will fail.
  shmall_under_ram     warn  shmall * PAGE_SIZE < MemTotal —
                             can't allocate enough shared
                             memory to back GPU IPC tensors
                             or large database buffer pools.
  shmmax_capped_low    warn  shmmax < 2 GiB on ≥16 GiB box —
                             explains "shared_buffers=8GB
                             fails to start".
  shmmni_low           accent shmmni < 4096 (distro default)
                             — segments limit may bite under
                             multi-tenant workloads.
  ok                   limits coherent with system RAM.
  unknown              /proc/sys/kernel unreadable.

stdlib only.
"""
from __future__ import annotations

import os
import re
from typing import Optional

NAME = "sysvipc_limits_audit"

DEFAULT_PROC_SYS_KERNEL = "/proc/sys/kernel"
DEFAULT_PROC_MEMINFO = "/proc/meminfo"

# 2 GiB threshold for shmmax_capped_low.
_TWO_GIB = 2 * 1024 * 1024 * 1024
# 16 GiB threshold above which a small shmmax is a problem.
_SIXTEEN_GIB = 16 * 1024 * 1024 * 1024
# Default distro shmmni floor.
_SHMMNI_DEFAULT = 4096

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


def read_limits(proc_sys: str = DEFAULT_PROC_SYS_KERNEL) -> dict:
    return {
        "shmmax": _read_int(os.path.join(proc_sys, "shmmax")),
        "shmall": _read_int(os.path.join(proc_sys, "shmall")),
        "shmmni": _read_int(os.path.join(proc_sys, "shmmni")),
        "msgmni": _read_int(os.path.join(proc_sys, "msgmni")),
    }


def _page_size() -> int:
    try:
        return os.sysconf("SC_PAGE_SIZE")
    except (ValueError, OSError):
        return 4096


def classify(limits: dict, mem_total: Optional[int],
             page_size: int) -> dict:
    shmmax = limits.get("shmmax")
    shmall = limits.get("shmall")
    shmmni = limits.get("shmmni")
    msgmni = limits.get("msgmni")

    if shmmax is None and shmall is None:
        return {"verdict": "unknown",
                "reason": (
                    "/proc/sys/kernel/shm* unreadable — "
                    "procfs unavailable or kernel built "
                    "without SYSVIPC.")}

    # err — shmmax = 0
    if shmmax == 0:
        return {"verdict": "shmmax_zero",
                "reason": (
                    "kernel.shmmax = 0 — SysV SHM effectively "
                    "disabled. CUDA-MPS, PostgreSQL, X11 IPC "
                    "will refuse to allocate any segment."),
                "shmmax": shmmax}

    # warn — shmall * PAGE_SIZE < MemTotal
    if (shmall is not None and mem_total is not None
            and shmall * page_size < mem_total):
        shmall_bytes = shmall * page_size
        return {
            "verdict": "shmall_under_ram",
            "reason": (
                f"shmall = {shmall} pages × {page_size} B = "
                f"{shmall_bytes / 2**30:.1f} GiB, but MemTotal "
                f"is {mem_total / 2**30:.1f} GiB. CUDA-MPS / "
                "DB shared_buffers can't grow past the cap."),
            "shmall_bytes": shmall_bytes,
            "mem_total": mem_total,
        }

    # warn — shmmax < 2 GiB on a ≥16 GiB box
    if (shmmax is not None and shmmax < _TWO_GIB
            and mem_total is not None
            and mem_total >= _SIXTEEN_GIB):
        return {
            "verdict": "shmmax_capped_low",
            "reason": (
                f"shmmax = {shmmax / 2**30:.2f} GiB on a "
                f"{mem_total / 2**30:.0f} GiB box — a single "
                "PostgreSQL / CUDA-MPS process can't claim "
                "more than that for SHM."),
            "shmmax": shmmax,
            "mem_total": mem_total,
        }

    # accent — shmmni below the modern default of 4096
    if shmmni is not None and shmmni < _SHMMNI_DEFAULT:
        return {
            "verdict": "shmmni_low",
            "reason": (
                f"shmmni = {shmmni} (< default {_SHMMNI_DEFAULT}) "
                "— total SHM segment count capped low. Bumps "
                "needed for many-tenant workloads."),
            "shmmni": shmmni,
        }

    return {"verdict": "ok",
            "reason": (
                f"shmmax = {(shmmax or 0) / 2**30:.1f} GiB ; "
                f"shmmni = {shmmni} ; msgmni = {msgmni} — "
                "limits coherent with system RAM.")}


def status(config: Optional[dict] = None,
           proc_sys: str = DEFAULT_PROC_SYS_KERNEL,
           proc_meminfo: str = DEFAULT_PROC_MEMINFO) -> dict:
    limits = read_limits(proc_sys)
    mem_total = parse_meminfo_total_bytes(
        _read_text(proc_meminfo) or "")
    page_size = _page_size()
    verdict = classify(limits, mem_total, page_size)
    return {
        "ok": verdict["verdict"] == "ok",
        "limits": limits,
        "mem_total": mem_total,
        "page_size": page_size,
        "verdict": verdict,
    }
