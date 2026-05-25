"""Module cgroup_v2_memory_peak_audit — per-cgroup memory.peak
vs ceilings + swap.peak (R&D #93.2).

cgroup_memevents_audit (existing) walks /sys/fs/cgroup,
reads memory.events / memory.swap.events / memory.peak, and
surfaces oom_in_unit / swap_failures / high_pressure based
on the *cumulative* events files. It does NOT :

  * compute the memory.peak / memory.max ratio (only collects
    peak as an info field for sorting).
  * read memory.swap.peak (separate file).
  * read memory.events.local (events that happened IN this
    cgroup, not propagated up from descendants).

This audit owns those gaps.

Reads :

  /sys/fs/cgroup/**/memory.peak
  /sys/fs/cgroup/**/memory.max          'max' or N bytes
  /sys/fs/cgroup/**/memory.high         'max' or N bytes
  /sys/fs/cgroup/**/memory.swap.peak    high-water swap bytes
  /sys/fs/cgroup/**/memory.events.local high/max/oom counts
                                        for THIS cgroup only.

Verdicts (worst-first) :

  peak_at_max               err   any cgroup with numeric
                                  memory.max where peak ≥
                                  98 % of max — sitting at
                                  the OOM ceiling.
  peak_at_high_throttling   warn  any cgroup whose
                                  events.local 'high' count
                                  > 0 AND peak ≥ memory.high
                                  — got throttled recently.
  swap_peak_active          accent ≥ 1 cgroup has
                                  memory.swap.peak > 0 — swap
                                  was used (unusual on a
                                  desktop with plenty of RAM).
  ok                       no peak/max ratio over 98 %, no
                           local high-throttle events, no
                           swap peaks.
  requires_root            cgroup tree unreadable.
  unknown                  no /sys/fs/cgroup or cgroup v1
                           setup.

stdlib only.
"""
from __future__ import annotations

import os
from typing import Optional

NAME = "cgroup_v2_memory_peak_audit"

DEFAULT_CGROUP_ROOT = "/sys/fs/cgroup"

# Threshold for peak_at_max.
_PEAK_MAX_RATIO = 0.98
# Cap how many cgroups we walk to keep bounded on hosts
# with thousands of transient systemd scopes.
_MAX_CGROUPS_WALKED = 5000


def _read_text(path: str) -> Optional[str]:
    try:
        with open(path, "r", encoding="utf-8") as fh:
            return fh.read().strip()
    except (OSError, PermissionError):
        return None


def _read_int(path: str) -> Optional[int]:
    t = _read_text(path)
    if t is None or t == "max":
        return None
    try:
        return int(t)
    except ValueError:
        return None


def parse_events_local(text: Optional[str]) -> dict:
    """memory.events.local format: 'key value' per line.
    Returns dict with at least high / max / oom_kill keys."""
    out: dict = {}
    if not text:
        return out
    for line in text.splitlines():
        parts = line.split()
        if len(parts) >= 2:
            try:
                out[parts[0]] = int(parts[1])
            except ValueError:
                continue
    return out


def controller_present(root: str) -> Optional[bool]:
    text = _read_text(os.path.join(root, "cgroup.controllers"))
    if text is None:
        return None
    return "memory" in text.split()


def walk_cgroups(root: str,
                 max_visit: int = _MAX_CGROUPS_WALKED
                 ) -> list:
    """Walk /sys/fs/cgroup, collect dicts with relevant fields
    for cgroups that have memory.peak readable."""
    out: list = []
    visited = 0
    for dirpath, _, files in os.walk(root):
        visited += 1
        if visited > max_visit:
            break
        if "memory.peak" not in files:
            continue
        peak = _read_int(
            os.path.join(dirpath, "memory.peak"))
        if peak is None:
            continue
        max_v = _read_int(
            os.path.join(dirpath, "memory.max"))
        high_v = _read_int(
            os.path.join(dirpath, "memory.high"))
        swap_peak = _read_int(
            os.path.join(dirpath, "memory.swap.peak"))
        events_local = parse_events_local(
            _read_text(
                os.path.join(dirpath, "memory.events.local")))
        out.append({
            "path": os.path.relpath(dirpath, root),
            "peak": peak,
            "max": max_v,
            "high": high_v,
            "swap_peak": swap_peak or 0,
            "events_local_high": events_local.get("high", 0),
            "events_local_max": events_local.get("max", 0),
        })
    return out


def classify(present: Optional[bool],
             cgroups: list) -> dict:
    if present is None:
        return {"verdict": "requires_root",
                "reason": (
                    "/sys/fs/cgroup/cgroup.controllers "
                    "unreadable — re-run as root.")}
    if not present:
        return {"verdict": "unknown",
                "reason": (
                    "memory controller absent from "
                    "cgroup.controllers — cgroup v1 host or "
                    "kernel built without "
                    "CONFIG_MEMCG.")}
    if not cgroups:
        return {"verdict": "ok",
                "reason": (
                    "No cgroups with readable memory.peak — "
                    "no per-leaf memory accounting active.")}

    # err — peak at max
    at_max: list = []
    for c in cgroups:
        if (c["max"] is not None
                and c["max"] > 0
                and c["peak"] / c["max"] >= _PEAK_MAX_RATIO):
            at_max.append(c)
    if at_max:
        names = [c["path"] for c in at_max]
        return {
            "verdict": "peak_at_max",
            "reason": (
                f"{len(at_max)} cgroup(s) with peak ≥ 98% "
                f"of memory.max (e.g. {names[:3]}). Sitting "
                "at the OOM ceiling — next allocation "
                "likely fails."),
            "cgroups": names}

    # warn — events.local high > 0 AND peak ≥ memory.high
    throttled: list = []
    for c in cgroups:
        if (c["events_local_high"] > 0
                and c["high"] is not None
                and c["peak"] >= c["high"]):
            throttled.append(c)
    if throttled:
        names = [c["path"] for c in throttled]
        return {
            "verdict": "peak_at_high_throttling",
            "reason": (
                f"{len(throttled)} cgroup(s) had local "
                f"memory.high throttling (e.g. {names[:3]}) "
                "and their peak reached the high threshold "
                "— pressure already biting."),
            "cgroups": names}

    # accent — any cgroup with swap.peak > 0
    swap: list = [c for c in cgroups if c["swap_peak"] > 0]
    if swap:
        names = sorted(
            (c["path"] for c in swap),
            key=lambda p: -[c["swap_peak"]
                             for c in swap
                             if c["path"] == p][0])
        return {
            "verdict": "swap_peak_active",
            "reason": (
                f"{len(swap)} cgroup(s) have non-zero "
                f"memory.swap.peak (e.g. {names[:3]}) — "
                "swap was used. Unusual on a homelab with "
                "plenty of RAM."),
            "cgroups": names}

    return {"verdict": "ok",
            "reason": (
                f"{len(cgroups)} cgroup(s) inspected ; "
                "no peak-at-max, no local high throttling, "
                "no swap peaks.")}


def status(config: Optional[dict] = None,
           root: str = DEFAULT_CGROUP_ROOT) -> dict:
    present = controller_present(root)
    cgroups = walk_cgroups(root) if present else []
    verdict = classify(present, cgroups)
    return {
        "ok": verdict["verdict"] == "ok",
        "cgroup_count": len(cgroups),
        "verdict": verdict,
    }
