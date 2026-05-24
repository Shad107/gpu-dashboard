"""Module sched_features_debugfs_audit — CFS feature flag
+ tuning-knob drift (R&D #85.4).

Gamer / low-latency tuning guides commonly tell users to
flip flags or tune knobs in /sys/kernel/debug/sched/ —
many of those tweaks silently survive reboot only via
hand-rolled systemd units.  When they fall out of sync
they produce mysterious frame-time and CUDA-kernel-launch
jitter regressions on the LLM rig.

This audit goes broader than the existing sched_audit
(which only checks WAKEUP_PREEMPTION) by walking the full
sched debugfs tuning surface :

  /sys/kernel/debug/sched/features
       Space-separated flags ; NAME = on, NO_NAME = off.
       Kernel defaults vary by version ; we encode the
       4.x-6.x common-case defaults.
  /sys/kernel/debug/sched/latency_ns           default 6_000_000
  /sys/kernel/debug/sched/min_granularity_ns   default 750_000
  /sys/kernel/debug/sched/wakeup_granularity_ns default 1_000_000
  /sys/kernel/debug/sched/migration_cost_ns    default 500_000

We do NOT touch WAKEUP_PREEMPTION — that's sched_audit's
turf.

Verdicts (worst first) :

  critical_sched_flags_off    HRTICK = NO  OR  START_DEBIT
                              = NO  (rare and harmful
                              changes that break fairness
                              and timer behaviour).
  sched_tuning_drifted        ≥1 CFS knob (latency_ns,
                              min_granularity_ns,
                              wakeup_granularity_ns,
                              migration_cost_ns) deviates
                              by > 50 % from the kernel
                              default.
  one_flag_non_default        ≥1 non-WAKEUP_PREEMPTION
                              flag drifted (informational
                              — a tweak the user made).
  ok                          defaults intact.
  requires_root               /sys/kernel/debug/sched
                              unreadable.
  unknown                     debugfs absent or no
                              sched/ subsystem.
"""
from __future__ import annotations

import os
from typing import Optional

DEFAULT_SCHED_DEBUG = "/sys/kernel/debug/sched"
DEFAULT_DEBUGFS = "/sys/kernel/debug"

# Critical flags whose default is ON ; if disabled, that's err.
_CRITICAL_ON_FLAGS = ("HRTICK", "START_DEBIT")

# Flags we care about (informational accent if drifted).
# WAKEUP_PREEMPTION is handled by sched_audit — exclude it.
_TRACKED_FLAGS = (
    "GENTLE_FAIR_SLEEPERS",
    "NEW_FAIR_SLEEPERS",
    "NEXT_BUDDY",
    "LAST_BUDDY",
    "CACHE_HOT_BUDDY",
    "ARCH_POWER",
    "TTWU_QUEUE",
    "HRTICK", "START_DEBIT",
)

# Default kernel values (ns).  Some kernels removed these
# in favour of EEVDF — gracefully treat absent as "not
# tunable on this kernel".
_TUNING_DEFAULTS = {
    "latency_ns":           6_000_000,
    "min_granularity_ns":   750_000,
    "wakeup_granularity_ns": 1_000_000,
    "migration_cost_ns":    500_000,
}

_DRIFT_RATIO = 0.50  # 50 %


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


def parse_features(text: Optional[str]) -> dict:
    """Returns {flag_name: bool} from
    'GENTLE_FAIR_SLEEPERS NO_NEXT_BUDDY ...' format."""
    if not text:
        return {}
    out: dict = {}
    for tok in text.split():
        if tok.startswith("NO_"):
            out[tok[3:]] = False
        else:
            out[tok] = True
    return out


def read_state(root: str = DEFAULT_SCHED_DEBUG,
                debugfs: str = DEFAULT_DEBUGFS
                ) -> tuple[Optional[dict], dict, str]:
    """Returns (features, tunings, read_state)."""
    features_text = _read_text(os.path.join(root, "features"))
    tunings: dict = {}
    for name in _TUNING_DEFAULTS:
        v = _read_int(os.path.join(root, name))
        if v is not None:
            tunings[name] = v
    if features_text is None and not tunings:
        # Unreadable — distinguish requires_root from unknown.
        if not os.path.isdir(debugfs):
            return (None, {}, "unknown")
        try:
            os.listdir(debugfs)
            debugfs_readable = True
        except (OSError, PermissionError):
            debugfs_readable = False
        if not debugfs_readable:
            return (None, {}, "requires_root")
        if not os.path.isdir(root):
            return (None, {}, "unknown")
        return (None, {}, "requires_root")

    features = parse_features(features_text)
    return (features, tunings, "ok")


def classify(features: Optional[dict],
             tunings: dict,
             read_state: str) -> dict:
    if read_state == "unknown":
        return {"verdict": "unknown",
                "reason": (
                    "/sys/kernel/debug/sched absent — "
                    "kernel without CFS debugfs surface.")}
    if read_state == "requires_root":
        return {"verdict": "requires_root",
                "reason": (
                    "/sys/kernel/debug is mode-700 — "
                    "re-run dashboard as root for the "
                    "sched feature / tuning inventory.")}

    features = features or {}

    # 1. err — critical-default-ON flags disabled
    off_critical = [
        f for f in _CRITICAL_ON_FLAGS
        if f in features and features[f] is False]
    if off_critical:
        return {"verdict": "critical_sched_flags_off",
                "reason": (
                    f"Critical scheduler flag(s) "
                    f"{','.join(off_critical)} disabled — "
                    "breaks fairness or timer behaviour."),
                "flags": off_critical}

    # 2. warn — tuning knob drifted > 50 %
    drifted_knobs = []
    for name, default in _TUNING_DEFAULTS.items():
        actual = tunings.get(name)
        if actual is None:
            continue
        if default == 0:
            continue
        delta_ratio = abs(actual - default) / default
        if delta_ratio > _DRIFT_RATIO:
            drifted_knobs.append({
                "name": name, "actual": actual,
                "default": default,
                "ratio": delta_ratio})
    if drifted_knobs:
        worst = max(drifted_knobs, key=lambda k: k["ratio"])
        return {"verdict": "sched_tuning_drifted",
                "reason": (
                    f"{worst['name']} = {worst['actual']} "
                    f"(default {worst['default']}, "
                    f"{worst['ratio']:.0%} drift) — "
                    "sub-millisecond fairness window "
                    "tweaked away from kernel default."),
                "knob": worst["name"],
                "actual": worst["actual"],
                "default": worst["default"]}

    # 3. accent — one tracked flag drifted (informational)
    # We need to know the "default" state per flag — most
    # of the tracked ones default to ON, except the BUDDY /
    # ARCH_POWER ones which vary. We accept any drift on
    # tracked flags as informational.
    drifted_flags = [
        f for f in _TRACKED_FLAGS
        if f in features and features[f] is False
        and f not in _CRITICAL_ON_FLAGS]
    if drifted_flags:
        return {"verdict": "one_flag_non_default",
                "reason": (
                    f"{','.join(drifted_flags)} disabled — "
                    "informational scheduler tweak."),
                "flags": drifted_flags}

    return {"verdict": "ok",
            "reason": (
                f"{len(features)} feature(s), "
                f"{len(tunings)} tunable(s) inspected ; "
                "defaults preserved.")}


def status(config: Optional[dict] = None,
           root: str = DEFAULT_SCHED_DEBUG,
           debugfs: str = DEFAULT_DEBUGFS) -> dict:
    features, tunings, read_state = read_state_helper(
        root, debugfs)
    verdict = classify(features, tunings, read_state)
    return {
        "ok": verdict["verdict"] not in (
            "unknown", "requires_root",
            "critical_sched_flags_off"),
        "read_state": read_state,
        "feature_count": len(features) if features else 0,
        "tuning_count": len(tunings),
        "tunings": tunings,
        "verdict": verdict,
    }


# Alias for backwards-compatible internal naming consistency.
read_state_helper = read_state
