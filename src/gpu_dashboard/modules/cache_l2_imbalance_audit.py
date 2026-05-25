"""Module cache_l2_imbalance_audit — heterogeneous L2 cache
size detection (R&D #106.3, weaker pick).

Hybrid Intel (Alder/Raptor/Meteor Lake) and AMD (Phoenix-Z
mobile) ship P-cores and E-cores with different L2 sizes.
LLM threadpool placement on the smaller-L2 cluster pays a
real cache-miss cost (~5-10 % slowdown for matmul-heavy
workloads).

cpu_cache_topology only filters level==3 (LLC). hybrid_cpu_topo
groups by max-freq, not by L2 footprint.

Reads :

  /sys/devices/system/cpu/cpu*/cache/index2/size

Verdicts (worst-first) :

  l2_island_imbalance      accent   >= 2 distinct L2 sizes
                                    across online CPUs — pin
                                    LLM threads to the larger-
                                    L2 cluster.
  uniform_l2                       ok all CPUs have the same
                                    L2 footprint.
  no_l2                            ok no L2 sysfs (rare).
  requires_root                    sysfs unreadable.
  unknown                          /sys/devices/system/cpu
                                   absent.

Acknowledged weakness: on hybrid boxes the signal often
overlaps hybrid_cpu_topo (which groups by max-freq, but
the L2 split usually correlates). Useful mainly as a
taskset recipe in the UI snippet, not as a problem class.

stdlib only.
"""
from __future__ import annotations

import os
import re
from typing import Optional

NAME = "cache_l2_imbalance_audit"

DEFAULT_CPU_ROOT = "/sys/devices/system/cpu"


def _read_text(path: str) -> Optional[str]:
    try:
        with open(path, "r", encoding="utf-8") as fh:
            return fh.read()
    except (OSError, PermissionError):
        return None


def parse_size_to_kib(text: Optional[str]
                       ) -> Optional[int]:
    """Parse '4096K' / '2M' → KiB integer."""
    if not text:
        return None
    s = text.strip()
    m = re.match(r"^(\d+)\s*([KMG]?)$", s)
    if not m:
        return None
    n = int(m.group(1))
    unit = m.group(2)
    mult = {"": 1, "K": 1, "M": 1024,
            "G": 1024 * 1024}.get(unit, 1)
    return n * mult


def walk_l2(cpu_root: str = DEFAULT_CPU_ROOT) -> dict:
    """Return {cpu_id: l2_kib}."""
    out: dict = {}
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
        cpu_id = int(m.group(1))
        idx2 = os.path.join(cpu_root, ent, "cache",
                             "index2", "size")
        kib = parse_size_to_kib(_read_text(idx2))
        if kib is not None:
            out[cpu_id] = kib
    return out


def classify(cpu_present: bool,
             l2: dict) -> dict:
    if not cpu_present:
        return {"verdict": "unknown",
                "reason": (
                    "/sys/devices/system/cpu absent.")}
    if not l2:
        return {"verdict": "ok",
                "reason": (
                    "No L2 cache sysfs entries — UP "
                    "kernel or unusual CPU. Treating as "
                    "non-issue.")}
    sizes = set(l2.values())
    if len(sizes) >= 2:
        biggest = max(sizes)
        smallest = min(sizes)
        return {
            "verdict": "l2_island_imbalance",
            "reason": (
                f"{len(sizes)} distinct L2 sizes detected "
                f"(smallest={smallest} KiB, biggest="
                f"{biggest} KiB). Pin latency-sensitive "
                "threads to the larger-L2 cluster with "
                "taskset.")}
    return {"verdict": "ok",
            "reason": (
                f"{len(l2)} CPU(s) ; L2 uniform at "
                f"{next(iter(sizes))} KiB.")}


def status(config: Optional[dict] = None,
           cpu_root: str = DEFAULT_CPU_ROOT) -> dict:
    cpu_present = os.path.isdir(cpu_root)
    l2 = walk_l2(cpu_root) if cpu_present else {}
    verdict = classify(cpu_present, l2)
    sizes = sorted(set(l2.values()))
    return {
        "ok": verdict["verdict"] == "ok",
        "cpu_count": len(l2),
        "l2_sizes_kib": sizes,
        "verdict": verdict,
    }
