"""Module sched_tunables_audit — /proc/sys/kernel/sched_*
tunable safety / sanity check (R&D #79.4).

Tuning guides routinely tell users to relax RT-bandwidth or
disable autogroup — both turn ssh/audio glitchy and the
unbounded variant can even deadlock the kernel.  This audit
reads every documented sched_* tunable, plus best-effort
peeks at /sys/kernel/debug/sched/features (root-only,
degrades gracefully), and emits a verdict aligned with the
specific risk class :

  unbounded_rt_runtime    sched_rt_runtime_us = -1
                          RT tasks can starve the system
                          indefinitely  →  kernel hang risk.
  autogroup_off           sched_autogroup_enabled = 0 on a
                          desktop session  →  one foreground
                          process can starve all others.
  rt_ratio_low            sched_rt_runtime / sched_rt_period
                          < 80 %  →  RT-throttling kicks in
                          early, audio / inference jitter.
  schedstats_off          sched_schedstats = 0
                          /proc/schedstat populated with
                          zeroes  →  other CFS audits broken.
  ok                      all tunables at sane defaults.
  requires_root           debug/sched not readable (best-
                          effort, the verdict is still
                          actionable).
  unknown                 /proc/sys/kernel/sched_*
                          unreadable entirely.

This audit is intentionally additive — it does NOT touch
/proc/sys/kernel/sched_features or any debug entry beyond
read-only inspection, and never writes.
"""
from __future__ import annotations

import os
from typing import Optional

DEFAULT_SCHED_ROOT = "/proc/sys/kernel"
DEFAULT_DEBUG_SCHED = "/sys/kernel/debug/sched"

# Tunables we read.  None means "not all kernels expose it".
_TUNABLES = (
    "sched_rt_runtime_us",
    "sched_rt_period_us",
    "sched_autogroup_enabled",
    "sched_child_runs_first",
    "sched_cfs_bandwidth_slice_us",
    "sched_deadline_period_max_us",
    "sched_deadline_period_min_us",
    "sched_energy_aware",
    "sched_rr_timeslice_ms",
    "sched_schedstats",
    "sched_util_clamp_max",
    "sched_util_clamp_min",
    "sched_util_clamp_min_rt_default",
)

# Thresholds
_RT_RATIO_FLOOR = 0.80


def _read_text(path: str) -> Optional[str]:
    try:
        with open(path, "r", encoding="utf-8") as fh:
            return fh.read().strip()
    except (OSError, PermissionError):
        return None


def _read_int(path: str) -> Optional[int]:
    s = _read_text(path)
    if s is None or s == "":
        return None
    try:
        return int(s)
    except ValueError:
        return None


def read_tunables(root: str = DEFAULT_SCHED_ROOT) -> dict:
    """Returns dict of tunable name -> int or None."""
    return {
        name: _read_int(os.path.join(root, name))
        for name in _TUNABLES
    }


def read_features(debug_root: str = DEFAULT_DEBUG_SCHED
                   ) -> Optional[list[str]]:
    """Reads /sys/kernel/debug/sched/features (root-gated).

    Returns the space-separated feature tokens (NO_FOO means
    feature disabled), or None on permission denial."""
    text = _read_text(os.path.join(debug_root, "features"))
    if text is None:
        return None
    return text.split()


def classify(tunables: dict,
             features: Optional[list[str]]) -> dict:
    if not tunables or all(v is None for v in tunables.values()):
        return {"verdict": "unknown",
                "reason": "/proc/sys/kernel/sched_* unreadable."}

    rt_rt = tunables.get("sched_rt_runtime_us")
    rt_p = tunables.get("sched_rt_period_us")
    autogroup = tunables.get("sched_autogroup_enabled")
    schedstats = tunables.get("sched_schedstats")

    # 1. err — unbounded RT runtime
    if rt_rt is not None and rt_rt == -1:
        return {"verdict": "unbounded_rt_runtime",
                "reason": (
                    "sched_rt_runtime_us = -1 — RT tasks can "
                    "starve the system indefinitely (kernel "
                    "hang risk).")}

    # 2. warn — autogroup off
    if autogroup is not None and autogroup == 0:
        return {"verdict": "autogroup_off",
                "reason": (
                    "sched_autogroup_enabled = 0 — desktop "
                    "session loses interactivity isolation.")}

    # 3. warn — RT ratio below floor
    if (rt_rt is not None and rt_p is not None
            and rt_p > 0 and rt_rt > 0):
        ratio = rt_rt / rt_p
        if ratio < _RT_RATIO_FLOOR:
            return {"verdict": "rt_ratio_low",
                    "reason": (
                        f"RT runtime/period ratio "
                        f"{ratio:.2%} — RT-throttling "
                        "kicks in early, audio jitter.")}

    # 4. accent — schedstats off
    if schedstats is not None and schedstats == 0:
        return {"verdict": "schedstats_off",
                "reason": (
                    "sched_schedstats = 0 — /proc/schedstat "
                    "populated with zeroes ; other CFS "
                    "audits are diagnostic-blind.")}

    # 5. ok
    return {"verdict": "ok",
            "reason": (
                "All sched_* tunables at sane defaults" + (
                    " ; debug/sched features readable."
                    if features is not None
                    else " ; debug/sched features not "
                         "readable as this UID."))}


def status(config: Optional[dict] = None,
           sched_root: str = DEFAULT_SCHED_ROOT,
           debug_sched: str = DEFAULT_DEBUG_SCHED) -> dict:
    tunables = read_tunables(sched_root)
    features = read_features(debug_sched)
    verdict = classify(tunables, features)
    return {
        "ok": verdict["verdict"] not in (
            "unknown", "unbounded_rt_runtime"),
        "tunables": tunables,
        "features_readable": features is not None,
        "feature_count": len(features) if features else 0,
        "verdict": verdict,
    }
