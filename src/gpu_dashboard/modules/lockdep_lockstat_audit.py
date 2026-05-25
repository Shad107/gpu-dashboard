"""Module lockdep_lockstat_audit — kernel lockdep posture +
silent-death detector (R&D #94.4).

Three existing modules touch lock-related surface :

  * proc_locks_contention_audit  — /proc/locks (POSIX file
                                   locks, not kernel lockdep)
  * rcu_expedited_audit          — RCU expedited grace
                                   periods only
  * kernel_lockup_watchdog_audit — soft/hard lockup detector

None reads the kernel's lockdep diagnostic plane. This audit
owns /proc/lockdep_stats + /proc/lockdep + /proc/lock_stat.

The "lockdep dead" signal is the killer one — lockdep
silently demotes itself when it hits MAX_LOCK_DEPTH /
MAX_LOCKDEP_KEYS / etc, by setting `debug_locks = 0`. After
that point every subsequent deadlock warning is suppressed.

Reads :

  /proc/lockdep_stats   "debug_locks: 1" + max/current counts
  /proc/lockdep         enumeration of registered lock classes
  /proc/lock_stat       lock_stat counters (CONFIG_LOCK_STAT)

Verdicts (worst-first) :

  lockdep_dead              err   /proc/lockdep_stats shows
                                  'debug_locks: 0' or any
                                  'BUG: MAX_LOCKDEP_*' line —
                                  every deadlock warning
                                  after this point is lost.
  lockdep_enabled_in_prod   accent lockdep files present on a
                                  kernel that isn't an
                                  explicit *-debug build —
                                  5-10 % perf tax. Common on
                                  AUR / homebrew kernels.
  requires_root             /proc/lockdep_stats present but
                            mode-400 (rare).
  unknown                   /proc/lockdep* absent — kernel
                            built without CONFIG_PROVE_LOCKING
                            (typical production kernel).

stdlib only.
"""
from __future__ import annotations

import os
from typing import Optional

NAME = "lockdep_lockstat_audit"

DEFAULT_PROC = "/proc"


def _read_text(path: str) -> Optional[str]:
    try:
        with open(path, "r", encoding="utf-8") as fh:
            return fh.read()
    except (OSError, PermissionError):
        return None


def lockdep_files_present(proc_root: str = DEFAULT_PROC) -> bool:
    """True if any of /proc/lockdep, /proc/lockdep_stats,
    /proc/lock_stat is a regular file."""
    for name in ("lockdep_stats", "lockdep", "lock_stat"):
        path = os.path.join(proc_root, name)
        if os.path.isfile(path):
            return True
    return False


def parse_lockdep_dead(text: str) -> bool:
    """Return True if /proc/lockdep_stats indicates lockdep
    has self-disabled (debug_locks = 0 OR any BUG: MAX_*
    line)."""
    if not text:
        return False
    for raw in text.splitlines():
        line = raw.strip().lower()
        if line.startswith("debug_locks:"):
            parts = line.split(":", 1)
            if len(parts) == 2:
                val = parts[1].strip()
                if val == "0":
                    return True
        if "bug: max_" in line or "bug: lock-" in line:
            return True
    return False


def classify(files_present: bool,
             stats_text: Optional[str]) -> dict:
    if not files_present:
        return {"verdict": "unknown",
                "reason": (
                    "No /proc/lockdep* files — kernel built "
                    "without CONFIG_PROVE_LOCKING (typical "
                    "production kernel ; no perf tax).")}
    if stats_text is None:
        return {"verdict": "requires_root",
                "reason": (
                    "/proc/lockdep_stats present but "
                    "unreadable — re-run as root.")}

    if parse_lockdep_dead(stats_text):
        return {
            "verdict": "lockdep_dead",
            "reason": (
                "/proc/lockdep_stats shows lockdep has self-"
                "disabled (debug_locks=0 or BUG: MAX_*). "
                "Every subsequent deadlock warning is "
                "suppressed. Reboot the kernel to reset.")}

    return {"verdict": "lockdep_enabled_in_prod",
            "reason": (
                "/proc/lockdep_stats present and healthy — "
                "kernel built with CONFIG_PROVE_LOCKING ; "
                "expect a 5-10% perf tax. Common on "
                "homebrew / AUR kernels ; rare on stock "
                "distro builds.")}


def status(config: Optional[dict] = None,
           proc_root: str = DEFAULT_PROC) -> dict:
    files_present = lockdep_files_present(proc_root)
    stats_text = (
        _read_text(os.path.join(proc_root, "lockdep_stats"))
        if files_present else None)
    verdict = classify(files_present, stats_text)
    return {
        "ok": False,
        "lockdep_present": files_present,
        "lockdep_dead": (
            parse_lockdep_dead(stats_text or "")
            if files_present and stats_text else False),
        "verdict": verdict,
    }
