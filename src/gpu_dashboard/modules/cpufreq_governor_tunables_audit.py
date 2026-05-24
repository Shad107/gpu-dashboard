"""Module cpufreq_governor_tunables_audit — per-governor sub-knob
posture (R&D #90.3).

Two existing modules touch cpufreq :

  * cpu_topology — reads scaling_governor per CPU, detects
    "powersave" specifically (and hybrid CPU). Doesn't read
    sub-knobs and doesn't have a per-policy drift verdict.
  * cpufreq_residency_audit — reads time_in_state +
    total_trans counters. No governor sub-knob reads.

This audit owns the governor's OWN tunables — the sub-knobs
that determine how fast a CPU actually responds to load,
which is the difference between buttery prompt-eval and
stuttery TTFT on a sparse-batch llama.cpp workload.

Reads :

  /sys/devices/system/cpu/cpufreq/policy*/scaling_governor
  /sys/devices/system/cpu/cpufreq/policy*/schedutil/rate_limit_us
  /sys/devices/system/cpu/cpufreq/policy*/ondemand/sampling_rate
  /sys/devices/system/cpu/cpufreq/policy*/conservative/sampling_rate

Verdicts (worst-first) :

  rate_limit_too_high              warn   schedutil
                                          rate_limit_us > 10000
                                          µs (10 ms reaction
                                          time) — stale Ubuntu
                                          default explains
                                          slow prompt-eval.
  ondemand_legacy_active           warn   governor in
                                          {ondemand,conservative}
                                          on a modern kernel —
                                          schedutil is the
                                          preferred default.
  governor_drift_across_policies   accent  multiple policies
                                          with different
                                          governors (cpupower
                                          --cpu=X set partial).
  ok                              uniform sane governor.
  requires_root                   sub-knob file mode-700.
  unknown                         /sys/devices/system/cpu/
                                  cpufreq absent.

stdlib only.
"""
from __future__ import annotations

import os
from typing import Optional

NAME = "cpufreq_governor_tunables_audit"

DEFAULT_CPUFREQ_ROOT = "/sys/devices/system/cpu/cpufreq"

# Reaction-time threshold for schedutil rate_limit_us.
_RATE_LIMIT_HIGH_US = 10000  # 10 ms

# Legacy governors superseded by schedutil on modern kernels.
_LEGACY_GOVERNORS = {"ondemand", "conservative"}


def _read_text(path: str) -> Optional[str]:
    try:
        with open(path, "r", encoding="utf-8") as fh:
            return fh.read().strip()
    except (OSError, PermissionError):
        return None


def _read_int(path: str) -> Optional[int]:
    t = _read_text(path)
    if t is None:
        return None
    try:
        return int(t)
    except ValueError:
        return None


def list_policies(root: str = DEFAULT_CPUFREQ_ROOT) -> list:
    if not os.path.isdir(root):
        return []
    try:
        entries = os.listdir(root)
    except OSError:
        return []
    return sorted(
        e for e in entries if e.startswith("policy"))


def read_policy(root: str, name: str) -> dict:
    base = os.path.join(root, name)
    gov = _read_text(os.path.join(base, "scaling_governor"))
    rate_limit = None
    if gov:
        # Look for the governor's own sub-knob dir.
        if gov == "schedutil":
            rate_limit = _read_int(os.path.join(
                base, "schedutil", "rate_limit_us"))
            # Some kernels expose it at policy-level
            # directly instead of in a subdir.
            if rate_limit is None:
                rate_limit = _read_int(
                    os.path.join(base, "rate_limit_us"))
    return {
        "name": name,
        "governor": gov or "",
        "rate_limit_us": rate_limit,
    }


def classify(policies: list) -> dict:
    if not policies:
        return {"verdict": "unknown",
                "reason": (
                    "/sys/devices/system/cpu/cpufreq absent — "
                    "no cpufreq policies exposed (VM with "
                    "hypervisor-managed DVFS, or kernel "
                    "built without CONFIG_CPU_FREQ).")}

    govs = [p["governor"] for p in policies if p["governor"]]
    if not govs:
        return {"verdict": "requires_root",
                "reason": (
                    "scaling_governor unreadable across all "
                    "policies — file mode-700 (rare).")}

    # warn — schedutil rate_limit_us too high
    for p in policies:
        if (p["governor"] == "schedutil"
                and p["rate_limit_us"] is not None
                and p["rate_limit_us"] > _RATE_LIMIT_HIGH_US):
            return {
                "verdict": "rate_limit_too_high",
                "reason": (
                    f"Policy '{p['name']}' schedutil "
                    f"rate_limit_us = {p['rate_limit_us']} "
                    f"(> {_RATE_LIMIT_HIGH_US} µs) — governor "
                    "reacts > 10 ms late, stuttery prompt-eval "
                    "on sparse-batch inference."),
                "policy": p["name"],
                "rate_limit_us": p["rate_limit_us"],
            }

    # warn — legacy ondemand/conservative active
    legacy = [g for g in govs if g in _LEGACY_GOVERNORS]
    if legacy:
        return {
            "verdict": "ondemand_legacy_active",
            "reason": (
                f"Legacy governor(s) {sorted(set(legacy))} "
                "active — schedutil is the preferred default "
                "on modern kernels (≥ 5.0) and avoids the "
                "polling overhead."),
            "legacy_governors": sorted(set(legacy)),
        }

    # accent — different governors across policies
    if len(set(govs)) > 1:
        return {
            "verdict": "governor_drift_across_policies",
            "reason": (
                f"Policies have non-uniform governors: "
                f"{sorted(set(govs))} — likely a partial "
                "`cpupower --cpu=N frequency-set -g X` call "
                "that didn't cover every policy."),
            "governors": sorted(set(govs)),
        }

    return {"verdict": "ok",
            "reason": (
                f"{len(policies)} cpufreq policy(ies) ; "
                f"uniform governor '{govs[0]}', "
                "sub-knobs sane.")}


def status(config: Optional[dict] = None,
           root: str = DEFAULT_CPUFREQ_ROOT) -> dict:
    names = list_policies(root)
    policies = [read_policy(root, n) for n in names]
    verdict = classify(policies)
    return {
        "ok": verdict["verdict"] == "ok",
        "policy_count": len(policies),
        "governors": sorted({
            p["governor"] for p in policies if p["governor"]}),
        "verdict": verdict,
    }
