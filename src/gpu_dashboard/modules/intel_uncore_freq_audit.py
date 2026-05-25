"""Module intel_uncore_freq_audit — Intel uncore (LLC/ring/mesh)
frequency posture (R&D #102.1).

Uncore frequency is independent of core P-states. On Intel
desktops it can be pinned low by firmware/BIOS, capping
GPU↔CPU LLC bandwidth during inference workloads (llama.cpp,
PyTorch). The intel_uncore_frequency driver exposes per-die
knobs :

  /sys/devices/system/cpu/intel_uncore_frequency/
    package_<pkg>_die_<die>/
      min_freq_khz          # user-set floor
      max_freq_khz          # user-set ceiling
      current_freq_khz      # observed
      initial_min_freq_khz  # silicon floor
      initial_max_freq_khz  # silicon ceiling

No existing module reads this surface — cpu_cppc_audit, hwp_epp,
pstate_audit, cpufreq_governor_tunables_audit, cpufreq_residency
all target *core* P-states.

Verdicts (worst-first) :

  uncore_max_clamped_hard      err     max_freq_khz <
                                       initial_max_freq_khz *
                                       0.7 — uncore clamped >
                                       30 % below silicon max,
                                       capping LLC bandwidth.
  uncore_stuck_at_min          warn    current_freq_khz ==
                                       min_freq_khz on every
                                       die (driver not scaling).
  uncore_max_clamped_soft      accent  max_freq_khz <
                                       initial_max_freq_khz
                                       (mild clamp).
  ok                                   uncore at silicon max.
  requires_root                        sysfs present but
                                       unreadable.
  unknown                              intel_uncore_frequency
                                       driver absent.

stdlib only.
"""
from __future__ import annotations

import os
from typing import Optional

NAME = "intel_uncore_freq_audit"

DEFAULT_UNCORE_ROOT = (
    "/sys/devices/system/cpu/intel_uncore_frequency")

_HARD_CLAMP_RATIO = 0.7


def _read_int(path: str) -> Optional[int]:
    try:
        with open(path, "r", encoding="utf-8") as fh:
            return int(fh.read().strip())
    except (OSError, PermissionError, ValueError):
        return None


def walk_dies(root: str = DEFAULT_UNCORE_ROOT) -> list:
    """Return list of dicts per package_*_die_*."""
    out: list = []
    if not os.path.isdir(root):
        return out
    try:
        entries = sorted(os.listdir(root))
    except OSError:
        return out
    for ent in entries:
        if not (ent.startswith("package_")
                and "_die_" in ent):
            continue
        d = os.path.join(root, ent)
        if not os.path.isdir(d):
            continue
        out.append({
            "name": ent,
            "min_freq_khz": _read_int(
                os.path.join(d, "min_freq_khz")),
            "max_freq_khz": _read_int(
                os.path.join(d, "max_freq_khz")),
            "current_freq_khz": _read_int(
                os.path.join(d, "current_freq_khz")),
            "initial_min_freq_khz": _read_int(
                os.path.join(d, "initial_min_freq_khz")),
            "initial_max_freq_khz": _read_int(
                os.path.join(d, "initial_max_freq_khz")),
        })
    return out


def classify(driver_present: bool,
             dies: list,
             readable: bool) -> dict:
    if not driver_present:
        return {"verdict": "unknown",
                "reason": (
                    "intel_uncore_frequency driver absent "
                    "— AMD CPU, virtualised host, or pre-"
                    "Skylake.")}
    if not readable:
        return {"verdict": "requires_root",
                "reason": (
                    "intel_uncore_frequency sysfs "
                    "unreadable — re-run as root.")}
    if not dies:
        return {"verdict": "unknown",
                "reason": (
                    "No package_*_die_* dirs under "
                    "intel_uncore_frequency — driver "
                    "loaded but no dies enumerated.")}

    # err — any die clamped hard
    hard = []
    for d in dies:
        mx = d["max_freq_khz"]
        init = d["initial_max_freq_khz"]
        if (mx is not None and init is not None
                and init > 0
                and mx < init * _HARD_CLAMP_RATIO):
            hard.append((d["name"], mx, init))
    if hard:
        sample = hard[0]
        return {
            "verdict": "uncore_max_clamped_hard",
            "reason": (
                f"{len(hard)} die(s) have max_freq_khz "
                f"clamped > 30% below silicon max "
                f"(e.g. {sample[0]} = {sample[1]} kHz "
                f"vs initial_max {sample[2]} kHz). LLC "
                "bandwidth capped during inference.")}

    # warn — uncore stuck at min across all dies
    stuck = []
    for d in dies:
        cur = d["current_freq_khz"]
        mn = d["min_freq_khz"]
        if cur is not None and mn is not None and cur == mn:
            stuck.append(d["name"])
    if len(stuck) == len(dies) and dies:
        return {
            "verdict": "uncore_stuck_at_min",
            "reason": (
                f"All {len(dies)} die(s) currently at "
                "uncore min_freq_khz — driver not scaling "
                "up under load.")}

    # accent — mild clamp
    soft = []
    for d in dies:
        mx = d["max_freq_khz"]
        init = d["initial_max_freq_khz"]
        if (mx is not None and init is not None
                and mx < init):
            soft.append((d["name"], mx, init))
    if soft:
        sample = soft[0]
        return {
            "verdict": "uncore_max_clamped_soft",
            "reason": (
                f"{len(soft)} die(s) have max_freq_khz "
                f"below silicon max ({sample[0]}: "
                f"{sample[1]} kHz < {sample[2]} kHz). "
                "Some headroom unused.")}

    return {"verdict": "ok",
            "reason": (
                f"{len(dies)} uncore die(s) at silicon "
                "max — full LLC bandwidth available.")}


def status(config: Optional[dict] = None,
           root: str = DEFAULT_UNCORE_ROOT) -> dict:
    driver_present = os.path.isdir(root)
    readable = driver_present and os.access(root, os.R_OK)
    dies = walk_dies(root) if readable else []
    verdict = classify(driver_present, dies, readable)
    return {
        "ok": verdict["verdict"] == "ok",
        "die_count": len(dies),
        "dies": [
            {"name": d["name"],
             "min_freq_khz": d["min_freq_khz"],
             "max_freq_khz": d["max_freq_khz"],
             "current_freq_khz": d["current_freq_khz"],
             "initial_max_freq_khz":
                 d["initial_max_freq_khz"]}
            for d in dies],
        "verdict": verdict,
    }
