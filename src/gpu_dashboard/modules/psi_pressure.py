"""Module psi_pressure — PSI pressure-stall correlator (R&D #32.1).

Linux PSI (Pressure Stall Information, kernel 4.20+, CONFIG_PSI=y)
exposes per-resource stall metrics under /proc/pressure/{cpu,memory,
io}. Each file has two lines:

  some avg10=<f> avg60=<f> avg300=<f> total=<microsec>
  full avg10=<f> avg60=<f> avg300=<f> total=<microsec>

`some` = % time at least one task was delayed for that resource
`full` = % time ALL non-idle tasks were delayed (severe — true stall)

For an LLM inference rig:

  CPU.some.avg10 > 5     host is CPU-contended right now ; advise
                          CPUAffinity to pin inference to a subset
  Memory.full.avg10 > 5  active swap/reclaim ; points back to
                          #32.4 swappiness, #29.8 LimitMEMLOCK,
                          #31.2 smaps_rollup chain
  IO.full.avg10 > 5      kernel IO bound (GGUF rereads from disk) ;
                          check #30.3 NVMe iosched + #29.8 mlock

This module reads all three resources, classifies each, picks the
worst as the system-wide verdict, and emits cross-references to the
shipped causal modules in each recommendation.

stdlib only.
"""
from __future__ import annotations

import os
import re
from typing import Optional


NAME = "psi_pressure"


_PSI_ROOT = "/proc/pressure"


_LINE_RE = re.compile(
    r"^(some|full)\s+"
    r"avg10=([\d.]+)\s+avg60=([\d.]+)\s+avg300=([\d.]+)\s+total=(\d+)"
)


def parse_psi(text: str) -> dict:
    out: dict = {}
    if not text:
        return out
    for line in text.splitlines():
        m = _LINE_RE.match(line.strip())
        if not m:
            continue
        out[m.group(1)] = {
            "avg10": float(m.group(2)),
            "avg60": float(m.group(3)),
            "avg300": float(m.group(4)),
            "total_us": int(m.group(5)),
        }
    return out


def read_resource(root: str, name: str) -> Optional[dict]:
    p = os.path.join(root, name)
    try:
        with open(p) as f:
            return parse_psi(f.read())
    except OSError:
        return None


_ELEVATED_THRESHOLD = 5.0      # some.avg10 ≥ 5 % → elevated
_THROTTLED_THRESHOLD = 5.0     # full.avg10 ≥ 5 % → throttled (severe)


_REC_CPU = (
    "# CPU contention. Pin inference to a subset of CPUs:\n"
    "# (see also shipped #31.3 cpu_topology for hybrid P/E advice)\n"
    "taskset -c 0-7 llama-server ...\n"
    "# Permanent via systemd Drop-In with CPUAffinity=0-7\n"
)

_REC_MEMORY = (
    "# Memory pressure. Check the cause→symptom chain:\n"
    "# 1. #32.4 vm_sysctl_audit  — vm.swappiness should be 1-10\n"
    "# 2. #29.8 rlimit_audit     — LimitMEMLOCK=infinity on the unit\n"
    "# 3. #31.2 proc_smaps       — confirm Swap field on the daemon\n"
    "# If host is under real memory pressure: add RAM or shrink ctx\n"
)

_REC_IO = (
    "# IO contention. The GGUF mmap is likely being reread from disk:\n"
    "# 1. #30.3 nvme_iosched — scheduler=none + read_ahead_kb=4096\n"
    "# 2. #29.8 rlimit_audit — mlock the model into RAM\n"
    "# 3. Check `iotop -o` for the offending process\n"
)


_RANK = {"ok": 0, "missing": 0, "elevated": 1, "throttled": 2}


def classify(resource: str, psi: Optional[dict]) -> dict:
    if not psi:
        return {"verdict": "missing",
                "reason": (f"/proc/pressure/{resource} absent — "
                            f"kernel pre-4.20 or CONFIG_PSI=n."),
                "recommendation": ""}
    some_10 = psi.get("some", {}).get("avg10", 0.0)
    full_10 = psi.get("full", {}).get("avg10", 0.0)
    if full_10 >= _THROTTLED_THRESHOLD:
        return {
            "verdict": "throttled",
            "reason": (f"{resource} full.avg10={full_10:.1f}% — ALL "
                       f"non-idle tasks were delayed by {resource} "
                       f"contention over the last 10 s. This is the "
                       f"severe stall case."),
            "recommendation": _rec_for(resource),
        }
    if some_10 >= _ELEVATED_THRESHOLD:
        return {
            "verdict": "elevated",
            "reason": (f"{resource} some.avg10={some_10:.1f}% — at "
                       f"least one task was delayed by {resource} "
                       f"contention {some_10:.1f}% of the last 10 s. "
                       f"Not severe yet, but worth pinning."),
            "recommendation": _rec_for(resource),
        }
    return {
        "verdict": "ok",
        "reason": (f"{resource} some.avg10={some_10:.2f}% — no "
                   f"meaningful stall on this resource."),
        "recommendation": "",
    }


def _rec_for(resource: str) -> str:
    return {"cpu": _REC_CPU, "memory": _REC_MEMORY,
             "io": _REC_IO}.get(resource, "")


def status(cfg=None) -> dict:
    if not os.path.isdir(_PSI_ROOT):
        return {"ok": False, "error": "psi_unavailable",
                "reason": (f"{_PSI_ROOT} not present — kernel pre-4.20 "
                            f"or CONFIG_PSI=n.")}
    resources: list = []
    worst = "ok"
    for name in ("cpu", "memory", "io"):
        psi = read_resource(_PSI_ROOT, name)
        v = classify(name, psi)
        if _RANK.get(v["verdict"], 0) > _RANK.get(worst, 0):
            worst = v["verdict"]
        resources.append({
            "resource": name,
            "psi": psi or {},
            "verdict": v,
        })
    return {"ok": True, "resources": resources,
            "worst_verdict": worst}
