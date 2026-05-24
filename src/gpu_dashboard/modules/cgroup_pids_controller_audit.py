"""Module cgroup_pids_controller_audit — per-cgroup pids.max
posture (R&D #91.1).

No existing module reads the pids controller :

  * cgroup_root_audit only enumerates controller presence
  * cgroup_memevents_audit covers memory.events
  * cgroup_io_stat_audit covers io.stat
  * pid_rlimits_audit / process_id_limits_audit cover the
    global kernel.pid_max + RLIMIT_NPROC, not per-cgroup
    pids.max ceilings.

A systemd-user-slice or container hitting its pids.max
manifests in the wild as "VS Code can't spawn a new pty" or
"docker exec returns fork/exec: resource temporarily
unavailable" with no kmsg trace — the only signal is
pids.events.max counting up.

Reads :

  /sys/fs/cgroup/cgroup.controllers      pids controller present
  /sys/fs/cgroup/**/pids.max             "max" or integer
  /sys/fs/cgroup/**/pids.current         current pid count
  /sys/fs/cgroup/**/pids.events          max <N>\\nmax.imposed <N>

Only cgroups with a *numeric* pids.max are inspected ; the
typical leaf inheriting "max" (unlimited) is silently OK.

Verdicts (worst-first) :

  pids_max_hit             err   any cgroup where
                                 pids.current == pids.max.
  pids_max_historic        warn  any cgroup with
                                 pids.events 'max' counter > 0
                                 (limit was hit in the past).
  pids_near_limit          accent any cgroup at > 80 % of
                                  pids.max.
  ok                       all numeric-cap cgroups healthy
                           OR no numeric caps in the tree.
  requires_root            cgroup tree unreadable.
  unknown                  pids controller missing from
                           cgroup.controllers.

stdlib only.
"""
from __future__ import annotations

import os
from typing import Optional

NAME = "cgroup_pids_controller_audit"

DEFAULT_CGROUP_ROOT = "/sys/fs/cgroup"

_NEAR_LIMIT_THRESHOLD = 0.8

# Cap the walk so the audit stays bounded even on
# pathological systemd trees with tens of thousands of
# scopes (containers, transient units).
_MAX_CGROUPS_WALKED = 5000


def _read_text(path: str) -> Optional[str]:
    try:
        with open(path, "r", encoding="utf-8") as fh:
            return fh.read().strip()
    except (OSError, PermissionError):
        return None


def _parse_pids_max(text: Optional[str]) -> Optional[int]:
    """'max' → None (unlimited) ; integer → int ; else None."""
    if text is None:
        return None
    t = text.strip()
    if not t or t == "max":
        return None
    try:
        return int(t)
    except ValueError:
        return None


def _parse_pids_events(text: Optional[str]) -> int:
    """Return the 'max' counter (number of times limit hit).
    Format: 'max <N>\\nmax.imposed <N>'."""
    if not text:
        return 0
    for line in text.splitlines():
        parts = line.split()
        if len(parts) >= 2 and parts[0] == "max":
            try:
                return int(parts[1])
            except ValueError:
                return 0
    return 0


def controller_present(root: str) -> Optional[bool]:
    text = _read_text(os.path.join(root, "cgroup.controllers"))
    if text is None:
        return None
    return "pids" in text.split()


def walk_cgroups(root: str,
                 max_visit: int = _MAX_CGROUPS_WALKED
                 ) -> list:
    """Walk the cgroup tree, return list of dicts with at
    least pids.* values. Only includes cgroups whose
    pids.max is numeric (non-'max')."""
    out: list = []
    visited = 0
    for dirpath, dirnames, filenames in os.walk(root):
        visited += 1
        if visited > max_visit:
            break
        if "pids.max" not in filenames:
            continue
        max_v = _parse_pids_max(
            _read_text(os.path.join(dirpath, "pids.max")))
        if max_v is None:
            continue
        cur_text = _read_text(
            os.path.join(dirpath, "pids.current"))
        events_text = _read_text(
            os.path.join(dirpath, "pids.events"))
        try:
            cur_v = int((cur_text or "0").strip())
        except ValueError:
            cur_v = 0
        out.append({
            "path": os.path.relpath(dirpath, root),
            "max": max_v,
            "current": cur_v,
            "max_events": _parse_pids_events(events_text),
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
                    "pids controller absent from "
                    "cgroup.controllers — cgroup v1 setup or "
                    "kernel built without CONFIG_CGROUP_PIDS.")}

    if not cgroups:
        return {"verdict": "ok",
                "reason": (
                    "No cgroup has a numeric pids.max cap — "
                    "all inherit 'max' (unlimited).")}

    # err — limit fully hit
    hit = [c for c in cgroups if c["current"] >= c["max"]]
    if hit:
        names = [c["path"] for c in hit]
        return {
            "verdict": "pids_max_hit",
            "reason": (
                f"{len(hit)} cgroup(s) at pids.max ceiling: "
                f"{names[:3]}. New fork() calls inside are "
                "currently failing."),
            "cgroups": names}

    # warn — historic hit
    historic = [c for c in cgroups if c["max_events"] > 0]
    if historic:
        names = [c["path"] for c in historic]
        return {
            "verdict": "pids_max_historic",
            "reason": (
                f"{len(historic)} cgroup(s) hit pids.max in "
                f"the past (e.g. {names[:3]}). pids.events "
                "max counter > 0 — expect intermittent "
                "fork() failures."),
            "cgroups": names}

    # accent — near limit
    near = [c for c in cgroups
            if c["current"] >= _NEAR_LIMIT_THRESHOLD * c["max"]]
    if near:
        names = [c["path"] for c in near]
        return {
            "verdict": "pids_near_limit",
            "reason": (
                f"{len(near)} cgroup(s) at > 80% of pids.max "
                f"(e.g. {names[:3]}). Consider raising the "
                "cap before it bites."),
            "cgroups": names}

    return {"verdict": "ok",
            "reason": (
                f"{len(cgroups)} cgroup(s) with numeric "
                "pids.max ; all healthy.")}


def status(config: Optional[dict] = None,
           root: str = DEFAULT_CGROUP_ROOT) -> dict:
    present = controller_present(root)
    cgroups = walk_cgroups(root) if present else []
    verdict = classify(present, cgroups)
    return {
        "ok": verdict["verdict"] == "ok",
        "cgroup_with_cap_count": len(cgroups),
        "verdict": verdict,
    }
