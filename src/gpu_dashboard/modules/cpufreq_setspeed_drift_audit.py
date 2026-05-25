"""Module cpufreq_setspeed_drift_audit — userspace governor
setspeed pin drift (R&D #106.4, weaker pick).

The 'userspace' cpufreq governor lets userspace pin a frequency
via /sys/devices/system/cpu/cpu*/cpufreq/scaling_setspeed.
Classic homelab footgun: a thermal-test or undervolt script
pinned 800 MHz hours ago and the user forgot. Scripts that
restore default governor cleanup the *governor* but rarely
the *setspeed*.

Acknowledged weakness: requires the rare userspace governor
to be active to fire a real verdict. Most homelabs run
schedutil / performance.

Reads :

  /sys/devices/system/cpu/cpu*/cpufreq/scaling_setspeed
  /sys/devices/system/cpu/cpu*/cpufreq/scaling_governor
  /sys/devices/system/cpu/cpu*/cpufreq/cpuinfo_max_freq

Verdicts (worst-first) :

  setspeed_pinned_low      warn    governor=userspace AND
                                   setspeed < cpuinfo_max * 0.5.
  setspeed_unused          accent  setspeed has a value but
                                   governor != userspace —
                                   stale leftover.
  ok                               cpufreq healthy or no
                                   anomaly.
  requires_root                    cpufreq unreadable.
  unknown                          cpufreq absent (virtualised).

stdlib only.
"""
from __future__ import annotations

import os
import re
from typing import Optional

NAME = "cpufreq_setspeed_drift_audit"

DEFAULT_CPU_ROOT = "/sys/devices/system/cpu"


def _read_text(path: str) -> Optional[str]:
    try:
        with open(path, "r", encoding="utf-8") as fh:
            return fh.read()
    except (OSError, PermissionError):
        return None


def _read_str(path: str) -> Optional[str]:
    t = _read_text(path)
    return t.strip() if t is not None else None


def _read_int(path: str) -> Optional[int]:
    t = _read_text(path)
    if t is None:
        return None
    try:
        return int(t.strip())
    except ValueError:
        return None


def walk_cpus(cpu_root: str = DEFAULT_CPU_ROOT) -> list:
    """Return list of {cpu_id, governor, setspeed, max_freq}."""
    out: list = []
    if not os.path.isdir(cpu_root):
        return out
    try:
        entries = sorted(os.listdir(cpu_root))
    except OSError:
        return out
    for ent in entries:
        m = re.match(r"^cpu(\d+)$", ent)
        if not m:
            continue
        d = os.path.join(cpu_root, ent, "cpufreq")
        if not os.path.isdir(d):
            continue
        out.append({
            "cpu_id": int(m.group(1)),
            "governor": _read_str(
                os.path.join(d, "scaling_governor")),
            "setspeed": _read_int(
                os.path.join(d, "scaling_setspeed")),
            "max_freq": _read_int(
                os.path.join(d, "cpuinfo_max_freq")),
        })
    return out


def classify(cpu_present: bool,
             cpufreq_present: bool,
             cpus: list) -> dict:
    if not cpu_present:
        return {"verdict": "unknown",
                "reason": (
                    "/sys/devices/system/cpu absent.")}
    if not cpufreq_present:
        return {"verdict": "unknown",
                "reason": (
                    "cpufreq subsystem absent — "
                    "virtualised host or fixed-freq CPU.")}

    # warn — userspace governor + pinned low
    pinned_low = [
        c for c in cpus
        if (c["governor"] == "userspace"
            and c["setspeed"] is not None
            and c["max_freq"] is not None
            and c["max_freq"] > 0
            and c["setspeed"] < c["max_freq"] * 0.5)]
    if pinned_low:
        sample = pinned_low[0]
        return {
            "verdict": "setspeed_pinned_low",
            "reason": (
                f"{len(pinned_low)} CPU(s) on userspace "
                f"governor pinned at "
                f"{sample['setspeed']} kHz vs max "
                f"{sample['max_freq']} kHz (< 50 %). "
                "Stale tuning script leftover ?")}

    # accent — setspeed value present but governor != userspace
    unused = [
        c for c in cpus
        if (c["setspeed"] is not None
            and c["setspeed"] > 0
            and c["governor"] is not None
            and c["governor"] != "userspace")]
    if unused:
        return {
            "verdict": "setspeed_unused",
            "reason": (
                f"{len(unused)} CPU(s) have non-zero "
                "scaling_setspeed but governor != "
                "'userspace' — value is inert leftover ; "
                "harmless but worth clearing.")}

    return {"verdict": "ok",
            "reason": (
                f"{len(cpus)} CPU(s) ; governor + "
                "setspeed coherent.")}


def status(config: Optional[dict] = None,
           cpu_root: str = DEFAULT_CPU_ROOT) -> dict:
    cpu_present = os.path.isdir(cpu_root)
    cpus = walk_cpus(cpu_root) if cpu_present else []
    cpufreq_present = any(
        c["governor"] is not None for c in cpus)
    verdict = classify(cpu_present, cpufreq_present, cpus)
    return {
        "ok": verdict["verdict"] == "ok",
        "cpu_count": len(cpus),
        "cpufreq_present": cpufreq_present,
        "verdict": verdict,
    }
