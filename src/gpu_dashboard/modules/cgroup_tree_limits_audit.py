"""Module cgroup_tree_limits_audit — cgroup.max.depth /
descendants exhaustion (R&D #108.4, weaker pick).

Acknowledged weak overlap (R&D #108 survey): cgroup_root_audit
*reads* max_depth and max_descendants into its payload but
never classifies on them — its verdicts look only at hybrid /
controllers / own_path. This module adds the classification.

Reads :

  /sys/fs/cgroup/cgroup.max.depth
  /sys/fs/cgroup/cgroup.max.descendants
  /sys/fs/cgroup/cgroup.stat                (nr_descendants)

cgroup.max.depth limits descendants per subtree depth. Lowering
it (< 5) breaks layered systemd userdb / container nesting.
cgroup.max.descendants together with nr_descendants reveals
runaway cgroup creation (runc leak, systemd-userdb-spam).

Verdicts (worst-first) :

  cgroup_descendants_near_cap   warn   nr_descendants /
                                       max.descendants > 0.8 —
                                       runaway nesting,
                                       likely a runc leak or
                                       userdb spam.
  cgroup_depth_capped_low       accent max.depth < 5 — rare,
                                       breaks nested systemd
                                       user slices.
  ok                                   plenty of headroom or
                                       limits unbounded.
  requires_root                        cgroup tree unreadable.
  unknown                              cgroup v2 not mounted.

stdlib only.
"""
from __future__ import annotations

import os
from typing import Optional

NAME = "cgroup_tree_limits_audit"

DEFAULT_ROOT = "/sys/fs/cgroup"

_NEAR_CAP_RATIO = 0.8
_DEPTH_MIN = 5


def _read_text(path: str) -> Optional[str]:
    try:
        with open(path, "r", encoding="utf-8") as fh:
            return fh.read()
    except (OSError, PermissionError):
        return None


def _read_str(path: str) -> Optional[str]:
    t = _read_text(path)
    return t.strip() if t is not None else None


def _to_int_or_none(s: Optional[str]) -> Optional[int]:
    """Parse 'max' or an int."""
    if s is None:
        return None
    s = s.strip()
    if s == "max":
        return None
    try:
        return int(s)
    except ValueError:
        return None


def parse_nr_descendants(stat_text: Optional[str]
                          ) -> Optional[int]:
    """First column of cgroup.stat 'nr_descendants <N>'."""
    if not stat_text:
        return None
    for line in stat_text.splitlines():
        parts = line.split()
        if len(parts) == 2 and parts[0] == "nr_descendants":
            try:
                return int(parts[1])
            except ValueError:
                return None
    return None


def classify(v2_present: bool,
             v2_readable: bool,
             max_depth: Optional[int],
             max_descendants: Optional[int],
             nr_descendants: Optional[int]) -> dict:
    if not v2_present:
        return {"verdict": "unknown",
                "reason": (
                    "cgroup v2 not mounted at "
                    "/sys/fs/cgroup.")}
    if not v2_readable:
        return {"verdict": "requires_root",
                "reason": (
                    "cgroup root unreadable — re-run "
                    "as root.")}

    # warn — descendants near cap
    if (max_descendants is not None
            and max_descendants > 0
            and nr_descendants is not None
            and nr_descendants / max_descendants
                > _NEAR_CAP_RATIO):
        return {
            "verdict": "cgroup_descendants_near_cap",
            "reason": (
                f"nr_descendants={nr_descendants} vs "
                f"max.descendants={max_descendants} "
                f"({nr_descendants / max_descendants:.0%} "
                "used). Runc leak or systemd-userdb spam "
                "likely.")}

    # accent — depth capped low
    if max_depth is not None and max_depth < _DEPTH_MIN:
        return {
            "verdict": "cgroup_depth_capped_low",
            "reason": (
                f"cgroup.max.depth={max_depth} (< "
                f"{_DEPTH_MIN}). Nested systemd user "
                "slices / sandboxed containers will fail "
                "to create.")}

    return {"verdict": "ok",
            "reason": (
                f"nr_descendants={nr_descendants} ; "
                f"max.descendants={max_descendants or 'max'}"
                f" ; max.depth={max_depth or 'max'}. "
                "Healthy.")}


def status(config: Optional[dict] = None,
           root: str = DEFAULT_ROOT) -> dict:
    v2_present = os.path.isfile(
        os.path.join(root, "cgroup.controllers"))
    v2_readable = (
        v2_present and os.access(root, os.R_OK))
    max_depth = _to_int_or_none(_read_str(
        os.path.join(root, "cgroup.max.depth")))
    max_descendants = _to_int_or_none(_read_str(
        os.path.join(root, "cgroup.max.descendants")))
    nr_descendants = parse_nr_descendants(_read_text(
        os.path.join(root, "cgroup.stat")))
    verdict = classify(v2_present, v2_readable, max_depth,
                       max_descendants, nr_descendants)
    return {
        "ok": verdict["verdict"] == "ok",
        "max_depth": max_depth,
        "max_descendants": max_descendants,
        "nr_descendants": nr_descendants,
        "verdict": verdict,
    }
