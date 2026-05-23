"""Module page_idle_tracking_audit — kernel page-idle tracking
audit (R&D #71.3).

/sys/kernel/mm/page_idle/bitmap is a bitmap interface that lets
userspace mark every physical page idle and later check which
ones were touched ; it's the engine behind tools like
`page-types`, `damon-test`, and CRIU's working-set estimator.

Combined with :
  /proc/kpagecount       (per-PFN map count ; 0400 root)
  /proc/sys/vm/page-cluster (LRU swap clustering tunable)

…this audit answers : is page-idle tracking usable on this host
right now, or is something gating it ?

Verdicts (priority order) :
  page_idle_disabled         /sys/kernel/mm/page_idle absent —
                               kernel built without
                               CONFIG_IDLE_PAGE_TRACKING ; no
                               idle-page workflows possible.
  bitmap_unreadable          /sys/kernel/mm/page_idle/bitmap
                               present but EPERM/EACCES from this
                               daemon (typical : 0600 root).
  requires_root              /proc/kpagecount present but
                               unreadable.
  ok                         framework usable and readable.
  unknown                    none of the surfaces present
                               (impossible normally — kept for
                               framework symmetry).

stdlib only.
"""
from __future__ import annotations

import os
from typing import Optional


NAME = "page_idle_tracking_audit"


_SYS_PAGE_IDLE = "/sys/kernel/mm/page_idle"
_SYS_PAGE_IDLE_BITMAP = "/sys/kernel/mm/page_idle/bitmap"
_PROC_KPAGECOUNT = "/proc/kpagecount"
_PROC_PAGE_CLUSTER = "/proc/sys/vm/page-cluster"


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


def can_read(path: str) -> Optional[bool]:
    """Returns True if readable, False if EACCES/EPERM, None if
    absent. Treats bitmap files specially : we don't actually
    *read* them (could be huge), only stat + open."""
    if not os.path.exists(path):
        return None
    try:
        with open(path, "rb") as f:
            f.read(1)
            return True
    except PermissionError:
        return False
    except OSError:
        return False


def classify(page_idle_present: bool,
              bitmap_present: bool,
              bitmap_readable: Optional[bool],
              kpagecount_present: bool,
              kpagecount_readable: Optional[bool],
              page_cluster: Optional[int]) -> dict:
    if not page_idle_present:
        return {"verdict": "page_idle_disabled",
                "reason": ("/sys/kernel/mm/page_idle absent — "
                          "kernel built without "
                          "CONFIG_IDLE_PAGE_TRACKING."),
                "recommendation": _recipe_disabled()}

    if bitmap_present and bitmap_readable is False:
        return {"verdict": "bitmap_unreadable",
                "reason": ("/sys/kernel/mm/page_idle/bitmap "
                          "present but not readable from this "
                          "process (0600 root)."),
                "recommendation": _recipe_bitmap()}

    if kpagecount_present and kpagecount_readable is False:
        return {"verdict": "requires_root",
                "reason": ("/proc/kpagecount unreadable as "
                          "non-root user."),
                "recommendation": _recipe_kpagecount()}

    if not bitmap_present and not kpagecount_present:
        return {"verdict": "unknown",
                "reason": ("Neither /sys/kernel/mm/page_idle/"
                          "bitmap nor /proc/kpagecount present."),
                "recommendation": ""}

    return {"verdict": "ok",
            "reason": (f"page_idle framework usable ; "
                      f"page-cluster = {page_cluster}."),
            "recommendation": ""}


def status(config=None,
            sys_page_idle: str = _SYS_PAGE_IDLE,
            sys_bitmap: str = _SYS_PAGE_IDLE_BITMAP,
            proc_kpagecount: str = _PROC_KPAGECOUNT,
            proc_page_cluster: str = _PROC_PAGE_CLUSTER) -> dict:
    page_idle_present = os.path.isdir(sys_page_idle)
    bitmap_present = os.path.exists(sys_bitmap)
    bitmap_readable = (can_read(sys_bitmap)
                              if bitmap_present else None)
    kpagecount_present = os.path.exists(proc_kpagecount)
    kpagecount_readable = (can_read(proc_kpagecount)
                                  if kpagecount_present else None)
    page_cluster = _read_int(proc_page_cluster)
    verdict = classify(page_idle_present, bitmap_present,
                          bitmap_readable, kpagecount_present,
                          kpagecount_readable, page_cluster)
    return {"ok": page_idle_present,
              "page_idle_present": page_idle_present,
              "bitmap_present": bitmap_present,
              "bitmap_readable": bitmap_readable,
              "kpagecount_present": kpagecount_present,
              "kpagecount_readable": kpagecount_readable,
              "page_cluster": page_cluster,
              "verdict": verdict}


# ── recovery recipes ────────────────────────────────────────────

def _recipe_disabled() -> str:
    return ("# Kernel built without page-idle tracking. Useful\n"
            "# for damon / working-set estimators. Rebuild with :\n"
            "#   CONFIG_IDLE_PAGE_TRACKING=y\n"
            "# Or install a distro kernel that enables it.\n")


def _recipe_bitmap() -> str:
    return ("# The bitmap is 0600 root :\n"
            "ls -l /sys/kernel/mm/page_idle/bitmap\n"
            "# Test working-set tracking from root :\n"
            "sudo head -c 8 /sys/kernel/mm/page_idle/bitmap | xxd\n")


def _recipe_kpagecount() -> str:
    return ("# /proc/kpagecount complements page_idle. Inspect\n"
            "# as root :\n"
            "sudo head -c 8 /proc/kpagecount | xxd\n")
