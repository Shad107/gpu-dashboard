"""Module perf_pmu_audit — perf PMU inventory (R&D #51.3).

Walks /sys/bus/event_source/devices/* — the kernel's "performance
monitoring unit" registry. Each PMU registers a `type` integer
(used as perf_event_attr.type), an optional `events/` directory
listing pre-defined event aliases, and an optional `format/` dir
describing the bitfield layout of perf_event_attr.config.

Common PMUs :
  software             type=1 — kernel software events (context-
                       switches, page-faults, cpu-migrations).
  tracepoint           type=2 — every static ftrace tracepoint.
  cpu / cpu_atom /     type=4..7 — hardware CPU PMU (one per
  cpu_core             core-type on hybrid Alder/Raptor/Lunar
                       Lake). Each exposes ~12-30 hardware events.
  breakpoint           type=5 — hw breakpoint counter.
  kprobe, uprobe       types 8/9 — dynamic tracing.
  msr                  type=10 — MSR-direct event source.
  power                type=11 — RAPL energy counters
                       (power/energy-pkg/, energy-dram/, etc.).
  uncore_imc_<N>       memory controller PMU (DDR bandwidth).
  uncore_cha_<N>       cache-home agent (LLC traffic, Intel).
  uncore_irp_<N>       integrated root port PMU (PCIe traffic).
  intel_pt             Intel Processor Trace (low-level branch
                       tracing).
  nvidia               NVIDIA GPU PMU (rare in upstream, present
                       in nvidia-smi-stats / data-center driver).

Verdicts (priority-ordered) :
  no_pmu          /sys/bus/event_source/devices empty or absent.
  pmu_inventory   ≥1 PMU registered — surface info (most boxes
                  always have software + tracepoint + msr).
  unknown         reserved.

stdlib only. This module is intentionally read-only / inventory ;
the actionable verdicts come from specific PMU-consuming modules
(e.g. shipped #43.1 irq_rates_audit reads /proc/interrupts but
not the PMU directly).
"""
from __future__ import annotations

import os
from typing import Optional


NAME = "perf_pmu_audit"


_SYS_EVENT_SOURCE = "/sys/bus/event_source/devices"


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


def list_pmus(sys_event: str = _SYS_EVENT_SOURCE) -> list:
    if not os.path.isdir(sys_event):
        return []
    out: list = []
    try:
        for name in sorted(os.listdir(sys_event)):
            d = os.path.join(sys_event, name)
            if not os.path.isdir(d):
                continue
            type_int = _read_int(os.path.join(d, "type"))
            nr_addr_filters = _read_int(
                os.path.join(d, "nr_addr_filters"))
            cpumask = (_read(os.path.join(d, "cpumask"))
                            or "").strip() or None
            # Count event aliases in events/ (if present).
            events_dir = os.path.join(d, "events")
            n_events = 0
            event_names: list = []
            if os.path.isdir(events_dir):
                try:
                    event_names = sorted(
                        n for n in os.listdir(events_dir)
                        if os.path.isfile(os.path.join(events_dir, n)))
                    n_events = len(event_names)
                except OSError:
                    pass
            # Format fields in format/ dir.
            format_dir = os.path.join(d, "format")
            n_format = 0
            if os.path.isdir(format_dir):
                try:
                    n_format = sum(1 for n in os.listdir(format_dir)
                                       if os.path.isfile(os.path.join(
                                           format_dir, n)))
                except OSError:
                    pass
            out.append({
                "name": name,
                "type": type_int,
                "nr_addr_filters": nr_addr_filters,
                "cpumask": cpumask,
                "event_count": n_events,
                "events": event_names[:30],
                "format_field_count": n_format,
                "category": classify_pmu(name),
            })
    except OSError:
        return []
    return out


def classify_pmu(name: str) -> str:
    """Bucket the PMU by its name prefix / suffix."""
    n = name.lower()
    if n in ("software", "tracepoint", "kprobe", "uprobe",
             "breakpoint"):
        return "kernel"
    if n.startswith("cpu") or n in ("cpu_core", "cpu_atom"):
        return "cpu_hardware"
    if n.startswith("uncore_imc"):
        return "memory_controller"
    if n.startswith("uncore_cha") or n.startswith("uncore_llc"):
        return "cache_agent"
    if n.startswith("uncore_irp") or n.startswith("uncore_iio") \
            or n.startswith("uncore_pcu"):
        return "uncore_io"
    if n.startswith("uncore_"):
        return "uncore_other"
    if n == "msr":
        return "msr"
    if n == "power":
        return "rapl_energy"
    if n.startswith("intel_pt"):
        return "intel_pt"
    if n.startswith("nvidia") or n.startswith("amdgpu"):
        return "gpu"
    return "other"


def classify(pmus: list) -> dict:
    if not pmus:
        return {"verdict": "no_pmu",
                "reason": ("/sys/bus/event_source/devices empty — "
                           "kernel perf framework not registering "
                           "any PMU."),
                "recommendation": ""}
    by_category: dict = {}
    for p in pmus:
        c = p["category"]
        by_category[c] = by_category.get(c, 0) + 1
    parts = ", ".join(f"{k}={v}"
                       for k, v in sorted(by_category.items()))
    return {"verdict": "pmu_inventory",
            "reason": (f"{len(pmus)} PMU device(s) registered. "
                       f"By category : {parts}."),
            "recommendation": ""}


def status(cfg=None) -> dict:
    if not os.path.isdir(_SYS_EVENT_SOURCE):
        return {
            "ok": False,
            "verdict": {"verdict": "no_pmu",
                         "reason": ("/sys/bus/event_source/devices "
                                    "absent."),
                         "recommendation": ""},
            "pmus": [],
        }
    pmus = list_pmus(_SYS_EVENT_SOURCE)
    verdict = classify(pmus)
    return {
        "ok": bool(pmus),
        "pmu_count": len(pmus),
        "pmus": pmus,
        "verdict": verdict,
    }
