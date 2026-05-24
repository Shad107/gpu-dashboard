"""Module process_id_limits_audit — process / thread / mmap
limit audit (R&D #73.1).

Three kernel knobs cap how many independent execution + memory
units the host can spawn :

  /proc/sys/kernel/pid_max       max PID value (default 4 194 304
                                   on Linux ≥ 5.x ; older kernels
                                   capped at 32 768)
  /proc/sys/kernel/threads-max   max total tasks (PID + threads)
                                   in the kernel — sized at boot
                                   based on detected RAM
  /proc/sys/vm/max_map_count     max VMAs per process (default
                                   65 530 ; Electron, Chromium,
                                   most GPU compute workloads
                                   trip this)

CUDA inference servers spawn thousands of host threads ; Chrome
DevTools opens many mmap regions ; ML pipelines fork worker
processes. A silent `ENOMEM` from any of these limits aborts a
multi-hour job with no useful trace.

Verdicts (priority order) :
  pid_exhaustion_imminent      Active PID count > 80 % of
                                 pid_max (immediate failure).
  threads_max_too_low          threads-max < 65 536 (legacy
                                 small-RAM boot sizing).
  pid_max_legacy_32k           pid_max ≤ 32 768 (pre-5.x
                                 default ; modern build should
                                 be ≥ 4M).
  max_map_count_too_low        max_map_count < 65 530 (kernel
                                 default ; not raised yet
                                 despite Electron-style
                                 workloads).
  ok                           all limits comfortable.
  unknown                      one of the sysctls absent.

stdlib only.
"""
from __future__ import annotations

import os
from typing import Optional


NAME = "process_id_limits_audit"


_PROC_PID_MAX = "/proc/sys/kernel/pid_max"
_PROC_THREADS_MAX = "/proc/sys/kernel/threads-max"
_PROC_MAX_MAP_COUNT = "/proc/sys/vm/max_map_count"
_PROC_ROOT = "/proc"


# Thresholds
_PID_EXHAUSTION_FRAC = 0.80
_THREADS_MAX_FLOOR = 65_536
_PID_MAX_LEGACY = 32_768
_MAX_MAP_COUNT_FLOOR = 65_530


def _read(path: str) -> Optional[str]:
    try:
        with open(path) as f:
            return f.read().strip()
    except OSError:
        return None


def _read_int(path: str) -> Optional[int]:
    t = _read(path)
    if t is None:
        return None
    try:
        return int(t)
    except ValueError:
        return None


def count_active_pids(proc_root: str = _PROC_ROOT) -> int:
    try:
        return sum(1 for n in os.listdir(proc_root)
                       if n.isdigit())
    except OSError:
        return 0


def classify(pid_max: Optional[int],
              threads_max: Optional[int],
              max_map_count: Optional[int],
              active_pids: int) -> dict:
    if (pid_max is None and threads_max is None
            and max_map_count is None):
        return {"verdict": "unknown",
                "reason": ("None of pid_max, threads-max, or "
                          "max_map_count readable."),
                "recommendation": ""}

    # 1) pid_exhaustion_imminent
    if (pid_max is not None
            and (active_pids / pid_max) > _PID_EXHAUSTION_FRAC):
        return {"verdict": "pid_exhaustion_imminent",
                "reason": (f"{active_pids} active PIDs / "
                          f"pid_max={pid_max} = "
                          f"{100*active_pids/pid_max:.1f}%."),
                "recommendation": _recipe_pid_exhaustion()}

    # 2) threads_max_too_low
    if (threads_max is not None
            and threads_max < _THREADS_MAX_FLOOR):
        return {"verdict": "threads_max_too_low",
                "reason": (f"kernel.threads-max = {threads_max} "
                          f"(floor {_THREADS_MAX_FLOOR}). CUDA "
                          f"/ inference workloads will hit "
                          f"EAGAIN."),
                "recommendation": _recipe_threads_max()}

    # 3) pid_max_legacy_32k
    if (pid_max is not None
            and pid_max <= _PID_MAX_LEGACY):
        return {"verdict": "pid_max_legacy_32k",
                "reason": (f"kernel.pid_max = {pid_max} (legacy "
                          f"32k limit). Modern Linux defaults "
                          f"to 4M."),
                "recommendation": _recipe_pid_max_legacy()}

    # 4) max_map_count_too_low
    if (max_map_count is not None
            and max_map_count < _MAX_MAP_COUNT_FLOOR):
        return {"verdict": "max_map_count_too_low",
                "reason": (f"vm.max_map_count = {max_map_count}"
                          f" (floor {_MAX_MAP_COUNT_FLOOR}). "
                          f"Electron / Chromium / GPU "
                          f"workloads need higher."),
                "recommendation": _recipe_max_map_count()}

    return {"verdict": "ok",
            "reason": (f"pid_max={pid_max} ; threads-max="
                      f"{threads_max} ; max_map_count="
                      f"{max_map_count} ; active PIDs="
                      f"{active_pids}."),
            "recommendation": ""}


def status(config=None,
            proc_pid_max: str = _PROC_PID_MAX,
            proc_threads_max: str = _PROC_THREADS_MAX,
            proc_max_map_count: str = _PROC_MAX_MAP_COUNT,
            proc_root: str = _PROC_ROOT) -> dict:
    pid_max = _read_int(proc_pid_max)
    threads_max = _read_int(proc_threads_max)
    max_map_count = _read_int(proc_max_map_count)
    active_pids = count_active_pids(proc_root)
    verdict = classify(pid_max, threads_max, max_map_count,
                          active_pids)
    return {"ok": (pid_max is not None
                          or threads_max is not None
                          or max_map_count is not None),
              "pid_max": pid_max,
              "threads_max": threads_max,
              "max_map_count": max_map_count,
              "active_pids": active_pids,
              "pid_usage_pct": (round(100*active_pids/pid_max, 2)
                                      if pid_max else None),
              "verdict": verdict}


# ── recovery recipes ────────────────────────────────────────────

def _recipe_pid_exhaustion() -> str:
    return ("# Active PIDs approaching pid_max. Identify the\n"
            "# fork-bomb suspect :\n"
            "ps aux --sort=-pid | head -20\n"
            "ps -eo user,pid,ppid,cmd --sort=user | \\\n"
            "  awk '{print $1}' | sort | uniq -c | sort -nr | head\n"
            "# Raise pid_max (max 2^22 = 4194304) :\n"
            "echo 4194304 | sudo tee /proc/sys/kernel/pid_max\n")


def _recipe_threads_max() -> str:
    return ("# Raise threads-max for inference workloads :\n"
            "echo 524288 | sudo tee /proc/sys/kernel/threads-max\n"
            "# Persist via /etc/sysctl.d/99-pid-limits.conf :\n"
            "echo 'kernel.threads-max=524288' \\\n"
            "  | sudo tee -a /etc/sysctl.d/99-pid-limits.conf\n")


def _recipe_pid_max_legacy() -> str:
    return ("# Legacy 32k pid_max — raise to modern default :\n"
            "echo 4194304 | sudo tee /proc/sys/kernel/pid_max\n"
            "echo 'kernel.pid_max=4194304' \\\n"
            "  | sudo tee -a /etc/sysctl.d/99-pid-limits.conf\n")


def _recipe_max_map_count() -> str:
    return ("# Raise vm.max_map_count for Electron / GPU apps :\n"
            "echo 1048576 | sudo tee /proc/sys/vm/max_map_count\n"
            "echo 'vm.max_map_count=1048576' \\\n"
            "  | sudo tee -a /etc/sysctl.d/99-pid-limits.conf\n")
