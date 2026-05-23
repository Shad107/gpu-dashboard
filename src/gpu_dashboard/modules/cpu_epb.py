"""Module cpu_epb — legacy MSR_IA32_ENERGY_PERF_BIAS auditor (R&D #42.4).

Intel CPUs since Sandy Bridge expose an Energy-Performance-Bias
hint via MSR_IA32_ENERGY_PERF_BIAS — Linux surfaces it at
/sys/devices/system/cpu/cpu*/power/energy_perf_bias as an integer
0..15 with these semantic anchors :

  0   performance       (max perf, ignore power)
  4   balance_performance
  6   normal / default
  8   balance_power
  15  powersave         (min perf, max power-save)

On HWP-active hosts (Skylake-X and later "modern" Intel with
hardware p-state control), shipped #36.4 hwp_epp covers the
EPP string — but on Haswell-EP / Broadwell-EP / Skylake-SP that
ship with HWP disabled by default, and on any HWP-disabled
configuration, this legacy EPB value still wins over EPP and
silently throttles 5-15 % below benchmark.

It also matters on AMD : recent kernels expose the same path for
amd_pstate-active mode with a similar 0..15 semantic.

Verdicts :
  uniform_powersave        every CPU reports EPB >= 8 (balance_power
                           or worse) — the kernel is biasing the
                           inference cores toward power-save.
  mixed_across_cpus        the EPB value differs across CPUs — likely
                           a leftover from a per-core powersave tool
                           (cpupower set-perf-bias, tuned, etc.) that
                           half-applied. Surface the inconsistency
                           because heterogeneous EPB makes scheduler
                           decisions unpredictable.
  ok                       uniform EPB ≤ 6 (normal or perf-leaning).
  epb_unavailable          /sys/.../energy_perf_bias absent on every
                           CPU — pre-Sandy-Bridge Intel, AMD without
                           amd_pstate-active, or hypervisor masks
                           the MSR.
  unknown                  /sys/devices/system/cpu unreadable.

stdlib only.
"""
from __future__ import annotations

import os
from typing import Optional


NAME = "cpu_epb"


_SYS_CPU = "/sys/devices/system/cpu"


# Anchor labels documented by Linux ; integer 0..15 is the kernel's
# "raw" view but most distros only set these specific anchors.
_EPB_LABEL = {
    0: "performance",
    4: "balance_performance",
    6: "normal",
    8: "balance_power",
    15: "powersave",
}


def epb_label(value: int) -> str:
    return _EPB_LABEL.get(value, f"raw_{value}")


def _read(p: str) -> Optional[str]:
    try:
        with open(p) as f:
            return f.read()
    except OSError:
        return None


def _read_int(p: str) -> Optional[int]:
    t = _read(p)
    if t is None:
        return None
    try:
        return int(t.strip())
    except ValueError:
        return None


def list_cpus(sys_cpu: str = _SYS_CPU) -> list:
    if not os.path.isdir(sys_cpu):
        return []
    out: list = []
    for name in sorted(os.listdir(sys_cpu)):
        if not (name.startswith("cpu")
                and len(name) > 3
                and name[3:].isdigit()):
            continue
        out.append(name)
    # Numeric sort to be safe across cpu10 vs cpu2.
    out.sort(key=lambda n: int(n[3:]))
    return out


def read_per_cpu_epb(sys_cpu: str = _SYS_CPU) -> list:
    out: list = []
    for name in list_cpus(sys_cpu):
        path = os.path.join(sys_cpu, name, "power",
                              "energy_perf_bias")
        v = _read_int(path)
        out.append({"cpu": int(name[3:]),
                     "epb": v,
                     "label": (epb_label(v) if v is not None
                                else None)})
    return out


_RECIPE_SET_NORMAL = (
    "# Bias EPB toward performance (or normal) on every online CPU :\n"
    "for c in /sys/devices/system/cpu/cpu*/power/energy_perf_bias; do\n"
    "  echo 4 | sudo tee $c   # 4 = balance_performance\n"
    "done\n"
    "# Or via cpupower :\n"
    "sudo cpupower set --perf-bias 4\n"
    "# Persist via systemd unit (no canonical sysctl path for EPB) :\n"
    "sudo tee /etc/systemd/system/cpu-epb.service <<'EOF'\n"
    "[Unit]\n"
    "Description=Set CPU EPB to balance_performance on boot\n"
    "After=multi-user.target\n"
    "[Service]\n"
    "Type=oneshot\n"
    "ExecStart=/usr/bin/cpupower set --perf-bias 4\n"
    "[Install]\n"
    "WantedBy=multi-user.target\n"
    "EOF\n"
    "sudo systemctl enable --now cpu-epb.service"
)

_RECIPE_UNIFY = (
    "# EPB is heterogeneous across cores — re-apply a single value\n"
    "# so the scheduler has a consistent power/perf bias to work\n"
    "# against :\n"
    "sudo cpupower set --perf-bias 4   # 4 = balance_performance"
)


_POWER_SAVE_THRESHOLD = 8  # >= 8 = power-leaning side of the dial


def classify(per_cpu: list) -> dict:
    if not per_cpu:
        return {"verdict": "unknown",
                "reason": "/sys/devices/system/cpu unreadable.",
                "recommendation": ""}
    with_epb = [c for c in per_cpu if c["epb"] is not None]
    if not with_epb:
        return {"verdict": "epb_unavailable",
                "reason": ("No CPU exposes "
                           "energy_perf_bias — pre-Sandy-Bridge "
                           "Intel, AMD without amd_pstate-active, "
                           "or hypervisor masks the MSR. Use "
                           "the HWP-EPP card (#36.4) instead."),
                "recommendation": ""}
    distinct = {c["epb"] for c in with_epb}
    if len(distinct) > 1:
        # Mixed values — surface the distribution.
        buckets: dict = {}
        for c in with_epb:
            buckets.setdefault(c["epb"], []).append(c["cpu"])
        parts = [f"{epb_label(k)}({k}) on {len(v)} CPU(s)"
                 for k, v in sorted(buckets.items())]
        return {"verdict": "mixed_across_cpus",
                "reason": ("EPB values differ across cores : "
                           + ", ".join(parts) + ". A heterogeneous "
                           "bias makes scheduler power/perf "
                           "decisions unpredictable."),
                "recommendation": _RECIPE_UNIFY}
    val = next(iter(distinct))
    if val >= _POWER_SAVE_THRESHOLD:
        return {"verdict": "uniform_powersave",
                "reason": (f"Every CPU reports EPB={val} "
                           f"({epb_label(val)}) — the kernel is "
                           f"power-biased ; inference cores will "
                           f"under-boost. Set EPB=4 "
                           f"(balance_performance) for LLM "
                           f"workloads."),
                "recommendation": _RECIPE_SET_NORMAL}
    return {"verdict": "ok",
            "reason": (f"Uniform EPB={val} ({epb_label(val)}) across "
                       f"{len(with_epb)} CPU(s) — perf-leaning bias."),
            "recommendation": ""}


def status(cfg=None) -> dict:
    per_cpu = read_per_cpu_epb(_SYS_CPU)
    verdict = classify(per_cpu)
    return {
        "ok": bool(per_cpu),
        "cpu_count": len(per_cpu),
        "epb_exposed_count": sum(1 for c in per_cpu
                                    if c["epb"] is not None),
        "per_cpu": per_cpu,
        "verdict": verdict,
    }
