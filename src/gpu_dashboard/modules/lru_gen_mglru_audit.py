"""Module lru_gen_mglru_audit — Multi-Generational LRU
(MGLRU) audit (R&D #68.2).

MGLRU is the new memory-reclaim engine merged in Linux 6.1
(replacing the classic active/inactive LRU). On systems with
non-trivial memory pressure or swap activity, MGLRU's behaviour
materially affects tail latency. Knobs that matter :

  /sys/kernel/mm/lru_gen/enabled     bitmask of MGLRU features.
                                       0 = classic LRU only.
  /sys/kernel/mm/lru_gen/min_ttl_ms  minimum age (ms) before a
                                       page generation can be
                                       evicted. 0 = no floor.
  /sys/kernel/debug/lru_gen           per-zone generation tables
                                       (debugfs ; root-only).

Plus context from :
  /proc/swaps                         swap configured + used?
  /proc/pressure/memory               PSI memory pressure totals.

Why on a homelab :

* RTX 3090 + tight system RAM is a classic ML rig profile.
  Under memory pressure, MGLRU disabled means swap thrash hits
  the GPU host process before it gets a chance to be evicted —
  inference latency spikes 10-100×.
* `min_ttl_ms=0` paired with a heavy swap workload leads to the
  kernel evicting pages aged < 1 s, which is the GPU process's
  own working set. Setting a small TTL (e.g. 500 ms) protects
  short-lived hot pages.

Verdicts (priority order) :
  mglru_disabled_with_swap_pressure  enabled bit 0 is 0 AND
                                       swap shows ≥100 MiB used
                                       (likely real reclaim).
  min_ttl_too_low                    enabled but min_ttl_ms is
                                       0 AND swap is in use —
                                       evicts hot pages.
  requires_root                      /sys/kernel/debug/lru_gen
                                       unreadable (debugfs is
                                       root-only).
  ok                                 MGLRU enabled, min_ttl
                                       sensible, or swap idle.
  unknown                            /sys/kernel/mm/lru_gen
                                       absent (kernel < 6.1 or
                                       CONFIG_LRU_GEN=n).

stdlib only.
"""
from __future__ import annotations

import os
from typing import Optional


NAME = "lru_gen_mglru_audit"


_SYS_LRU_GEN = "/sys/kernel/mm/lru_gen"
_DEBUG_LRU_GEN = "/sys/kernel/debug/lru_gen"
_PROC_SWAPS = "/proc/swaps"
_PROC_PSI_MEMORY = "/proc/pressure/memory"

_SWAP_USED_THRESHOLD_KIB = 100 * 1024     # 100 MiB
_PSI_FULL_AVG60_THRESHOLD = 5.0           # 5 %
_MIN_TTL_OK_MS = 1000                     # 1 s


def _read(path: str) -> Optional[str]:
    try:
        with open(path) as f:
            return f.read().strip()
    except OSError:
        return None


def read_enabled(path: str = None) -> Optional[int]:
    p = path or os.path.join(_SYS_LRU_GEN, "enabled")
    t = _read(p)
    if t is None:
        return None
    try:
        return int(t, 0)
    except ValueError:
        return None


def read_min_ttl(path: str = None) -> Optional[int]:
    p = path or os.path.join(_SYS_LRU_GEN, "min_ttl_ms")
    t = _read(p)
    if t is None:
        return None
    try:
        return int(t)
    except ValueError:
        return None


def read_swap_used_kib(proc_swaps: str = _PROC_SWAPS) -> int:
    """Returns total Used kilobytes across all swap entries."""
    total = 0
    try:
        with open(proc_swaps) as f:
            lines = f.readlines()
    except OSError:
        return 0
    for ln in lines[1:]:
        parts = ln.split()
        if len(parts) < 4:
            continue
        try:
            total += int(parts[3])
        except ValueError:
            continue
    return total


def read_psi_memory_full_avg60(
        path: str = _PROC_PSI_MEMORY) -> Optional[float]:
    txt = _read(path)
    if txt is None:
        return None
    for ln in txt.splitlines():
        if ln.startswith("full"):
            # "full avg10=0.00 avg60=0.00 avg300=0.00 total=..."
            for tok in ln.split():
                if tok.startswith("avg60="):
                    try:
                        return float(tok.split("=", 1)[1])
                    except ValueError:
                        return None
    return None


def debug_lru_gen_readable(
        path: str = _DEBUG_LRU_GEN) -> Optional[bool]:
    """Returns True if readable, False if EACCES, None if absent."""
    if not os.path.exists(path):
        return None
    try:
        with open(path):
            return True
    except PermissionError:
        return False
    except OSError:
        return False


def classify(enabled: Optional[int],
              min_ttl: Optional[int],
              swap_used_kib: int,
              psi_full_avg60: Optional[float],
              debug_readable: Optional[bool],
              mglru_present: bool) -> dict:
    if not mglru_present:
        return {"verdict": "unknown",
                "reason": ("/sys/kernel/mm/lru_gen absent — kernel "
                          "< 6.1 or built without CONFIG_LRU_GEN."),
                "recommendation": _recipe_unknown()}

    swap_active = swap_used_kib >= _SWAP_USED_THRESHOLD_KIB
    psi_pressured = ((psi_full_avg60 or 0.0)
                          >= _PSI_FULL_AVG60_THRESHOLD)

    # 1) mglru_disabled_with_swap_pressure
    if enabled is not None and (enabled & 1) == 0:
        if swap_active or psi_pressured:
            return {"verdict":
                        "mglru_disabled_with_swap_pressure",
                    "reason": (f"MGLRU disabled "
                              f"(enabled = {enabled}) while swap "
                              f"is {swap_used_kib // 1024} MiB "
                              f"used / PSI memory full avg60 = "
                              f"{psi_full_avg60 or 0.0}%."),
                    "recommendation": _recipe_enable_mglru()}

    # 2) min_ttl_too_low
    if enabled is not None and (enabled & 1) == 1:
        if (min_ttl == 0) and swap_active:
            return {"verdict": "min_ttl_too_low",
                    "reason": (f"MGLRU enabled but min_ttl_ms = 0 "
                              f"with swap active "
                              f"({swap_used_kib // 1024} MiB "
                              f"used). Hot pages may be evicted "
                              f"prematurely."),
                    "recommendation": _recipe_min_ttl()}

    # 3) requires_root — debugfs gating
    if debug_readable is False:
        return {"verdict": "requires_root",
                "reason": ("/sys/kernel/debug/lru_gen is debugfs "
                          "(root-only) — running unprivileged. "
                          "Per-generation tables hidden."),
                "recommendation": _recipe_requires_root()}

    return {"verdict": "ok",
            "reason": (f"MGLRU enabled = {enabled} ; "
                      f"min_ttl_ms = {min_ttl} ; "
                      f"swap_used = {swap_used_kib // 1024} MiB ; "
                      f"PSI full avg60 = "
                      f"{psi_full_avg60 or 0.0}."),
            "recommendation": ""}


def status(config=None,
            sys_lru_gen: str = _SYS_LRU_GEN,
            debug_lru_gen: str = _DEBUG_LRU_GEN,
            proc_swaps: str = _PROC_SWAPS,
            proc_psi: str = _PROC_PSI_MEMORY) -> dict:
    mglru_present = os.path.isdir(sys_lru_gen)
    enabled = read_enabled(os.path.join(sys_lru_gen, "enabled"))
    min_ttl = read_min_ttl(os.path.join(sys_lru_gen, "min_ttl_ms"))
    swap_used = read_swap_used_kib(proc_swaps)
    psi = read_psi_memory_full_avg60(proc_psi)
    debug_readable = debug_lru_gen_readable(debug_lru_gen)
    verdict = classify(enabled, min_ttl, swap_used, psi,
                          debug_readable, mglru_present)
    return {"ok": mglru_present,
              "mglru_present": mglru_present,
              "enabled": enabled,
              "min_ttl_ms": min_ttl,
              "swap_used_kib": swap_used,
              "psi_full_avg60": psi,
              "debug_lru_gen_readable": debug_readable,
              "verdict": verdict}


# ── recovery recipes ────────────────────────────────────────────

def _recipe_unknown() -> str:
    return ("# MGLRU was merged in Linux 6.1. Check your kernel :\n"
            "uname -r\n"
            "grep CONFIG_LRU_GEN /boot/config-$(uname -r)\n")


def _recipe_enable_mglru() -> str:
    return ("# Enable MGLRU and its companion features :\n"
            "echo y | sudo tee /sys/kernel/mm/lru_gen/enabled\n"
            "# Or with all features (bit 0 = base, bit 1 = mm\n"
            "# walk, bit 2 = non-leaf PMD young) :\n"
            "echo 7 | sudo tee /sys/kernel/mm/lru_gen/enabled\n"
            "# Persist via a tmpfiles.d snippet for boot-time apply.\n")


def _recipe_min_ttl() -> str:
    return ("# Protect short-lived hot pages by setting a small\n"
            "# minimum generation age :\n"
            "echo 1000 | sudo tee /sys/kernel/mm/lru_gen/min_ttl_ms\n"
            "# 500-2000 ms covers most desktop/inference workloads.\n"
            "# Verify under load :\n"
            "watch -n2 'cat /proc/pressure/memory'\n")


def _recipe_requires_root() -> str:
    return ("# Per-zone MGLRU generation tables live in debugfs :\n"
            "sudo cat /sys/kernel/debug/lru_gen\n"
            "# Useful for debugging slow reclaim under pressure.\n")
