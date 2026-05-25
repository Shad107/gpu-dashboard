"""Module khugepaged_pressure_audit — THP collapse failure
delta tracking (R&D #92.3).

thp_audit (existing) reads only
/sys/kernel/mm/transparent_hugepage/khugepaged/
scan_sleep_millisecs for static config posture. It does NOT
read the actual collapse counters or delta-track them.
vmstat_reclaim_pressure_audit deltas reclaim counters
(pgsteal/pgscan/oom), not THP collapse.

This audit owns the per-window collapse-attempt-vs-failure
ratio. A failing collapse storm during model-load can stall
GPU feeder threads for hundreds of milliseconds even when
the static THP config looks "enabled".

Reads :

  /proc/vmstat                              thp_collapse_alloc,
                                            thp_collapse_alloc_failed,
                                            thp_fault_fallback,
                                            thp_split_*
  /sys/kernel/mm/transparent_hugepage/
   khugepaged/{pages_collapsed,
              max_ptes_none, defrag,
              scan_sleep_millisecs}        config snapshot
  $XDG_STATE_HOME/gpu-dashboard/
   khugepaged_prev.json                    delta state

Verdicts (worst-first) :

  collapse_failing_hot  err   Δthp_collapse_alloc_failed
                              dominates Δthp_collapse_alloc
                              (more than 2x) on non-trivial
                              activity (≥ 50 attempts in
                              window) — defrag thrashing
                              under memory pressure.
  ok                    healthy ratio or no activity since
                        last sample.
  unknown               no prior snapshot OR khugepaged dir
                        absent (THP disabled in kernel).

stdlib only.
"""
from __future__ import annotations

import json
import os
import re
from typing import Optional

NAME = "khugepaged_pressure_audit"

DEFAULT_VMSTAT = "/proc/vmstat"
DEFAULT_KHUGEPAGED = (
    "/sys/kernel/mm/transparent_hugepage/khugepaged")

# Minimum collapse attempts in the window before ratio is
# meaningful. Avoids firing on idle hosts.
_MIN_ACTIVITY = 50
# Failure ratio threshold (failures > 2 * successes).
_FAIL_RATIO_THRESHOLD = 2.0


def _default_state_path() -> str:
    base = (os.environ.get("XDG_STATE_HOME")
            or os.path.expanduser("~/.local/state"))
    return os.path.join(
        base, "gpu-dashboard", "khugepaged_prev.json")


_COUNTERS = (
    "thp_collapse_alloc",
    "thp_collapse_alloc_failed",
    "thp_fault_fallback",
    "thp_split_page",
)


def _read_text(path: str) -> Optional[str]:
    try:
        with open(path, "r", encoding="utf-8") as fh:
            return fh.read()
    except (OSError, PermissionError):
        return None


def _read_int(path: str) -> Optional[int]:
    t = _read_text(path)
    if not t:
        return None
    try:
        return int(t.strip())
    except ValueError:
        return None


def parse_vmstat_thp(text: str) -> dict:
    out: dict = {}
    if not text:
        return out
    for line in text.splitlines():
        parts = line.split()
        if len(parts) != 2:
            continue
        if parts[0] in _COUNTERS:
            try:
                out[parts[0]] = int(parts[1])
            except ValueError:
                pass
    return out


def read_khugepaged_config(
        root: str = DEFAULT_KHUGEPAGED) -> dict:
    """Read config knobs (informational, not for verdict)."""
    return {
        "pages_collapsed": _read_int(
            os.path.join(root, "pages_collapsed")),
        "max_ptes_none": _read_int(
            os.path.join(root, "max_ptes_none")),
        "scan_sleep_ms": _read_int(
            os.path.join(root, "scan_sleep_millisecs")),
        "alloc_sleep_ms": _read_int(
            os.path.join(root, "alloc_sleep_millisecs")),
    }


def load_prev(path: str) -> Optional[dict]:
    if not os.path.isfile(path):
        return None
    try:
        with open(path, "r", encoding="utf-8") as fh:
            data = json.load(fh)
        if isinstance(data, dict) and "counters" in data:
            return data
    except (OSError, json.JSONDecodeError):
        return None
    return None


def save_state(path: str, counters: dict) -> None:
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as fh:
            json.dump({"counters": counters}, fh)
    except OSError:
        pass


def compute_deltas(current: dict,
                   prev: Optional[dict]) -> dict:
    prev_counters = (prev or {}).get("counters", {})
    return {
        k: current.get(k, 0) - prev_counters.get(k, 0)
        for k in _COUNTERS
    }


def classify(deltas: dict, has_prev: bool,
             khugepaged_present: bool) -> dict:
    if not khugepaged_present:
        return {"verdict": "unknown",
                "reason": (
                    "/sys/kernel/mm/transparent_hugepage/"
                    "khugepaged absent — kernel built without "
                    "CONFIG_TRANSPARENT_HUGEPAGE or THP "
                    "disabled.")}
    if not has_prev:
        return {"verdict": "unknown",
                "reason": (
                    "No prior /proc/vmstat THP snapshot — "
                    "first invocation, baseline saved. Re-"
                    "check in a few minutes for deltas.")}

    success = max(0, deltas.get("thp_collapse_alloc", 0))
    failed = max(0, deltas.get(
        "thp_collapse_alloc_failed", 0))
    total = success + failed

    if (total >= _MIN_ACTIVITY
            and failed > _FAIL_RATIO_THRESHOLD * success):
        return {
            "verdict": "collapse_failing_hot",
            "reason": (
                f"khugepaged failed {failed} of {total} "
                "collapse attempts since last sample — "
                "memory fragmentation thrashing. GPU feeder "
                "threads may stall while khugepaged defrags."),
            "failed": failed,
            "succeeded": success,
        }

    return {"verdict": "ok",
            "reason": (
                f"khugepaged delta : {success} succeeded, "
                f"{failed} failed (window total = {total}).")}


def status(config: Optional[dict] = None,
           vmstat_path: str = DEFAULT_VMSTAT,
           khugepaged_root: str = DEFAULT_KHUGEPAGED,
           state_path: Optional[str] = None) -> dict:
    if state_path is None:
        state_path = _default_state_path()
    current = parse_vmstat_thp(
        _read_text(vmstat_path) or "")
    cfg = read_khugepaged_config(khugepaged_root)
    prev = load_prev(state_path)
    has_prev = prev is not None
    deltas = compute_deltas(current, prev)
    khugepaged_present = os.path.isdir(khugepaged_root)
    verdict = classify(deltas, has_prev, khugepaged_present)
    # Persist for next run, but only if directory exists
    # (avoid littering on systems without THP).
    if khugepaged_present:
        to_save = {k: current.get(k, 0) for k in _COUNTERS}
        save_state(state_path, to_save)
    return {
        "ok": verdict["verdict"] == "ok",
        "has_prev_snapshot": has_prev,
        "khugepaged_present": khugepaged_present,
        "max_ptes_none": cfg["max_ptes_none"],
        "scan_sleep_ms": cfg["scan_sleep_ms"],
        "verdict": verdict,
    }
