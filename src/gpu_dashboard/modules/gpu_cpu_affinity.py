"""Module gpu_cpu_affinity — GPU↔CPU PCIe local_cpulist advisor (R&D #37.2).

Shipped #35.3 numa_placement operates at the NUMA-node granularity:
"is your daemon split across nodes?". This module is finer-grain —
each NVIDIA PCIe device exposes /sys/bus/pci/devices/<bdf>/{
local_cpulist, local_cpus, numa_node}, which tells the kernel
exactly which CPUs are nearest the GPU's PCIe root complex. On
multi-CCD AMD (e.g. EPYC, Threadripper) or multi-socket Intel,
half (or fewer) of the CPUs may be "local" to a given GPU — the
others traverse Infinity Fabric / UPI / GMI for every PCIe-DMA
request, paying significant latency.

Verdicts:
  no_gpus               no NVIDIA VGA devices found
  single_node_affinity  local_cpulist == online — single-socket/
                         single-CCD host where preference is moot
  constrained_affinity  local_cpulist is a proper subset of online —
                         the LLM daemon will benefit from pinning to
                         the local set ; recipe drops a systemd
                         CPUAffinity= Drop-In
  unset                 PCI device has no local_cpulist (rare —
                         old kernels or weird VMs)
  unknown               can't enumerate /sys/bus/pci/devices

Recipe takes the GPU's local_cpulist verbatim and drops it as
CPUAffinity= in a per-unit Drop-In. Companion to #35.3 (which uses
node-level granularity) and #31.3 (which already knows about hybrid
P/E topology). Stacks naturally with shipped numactl recipes.

stdlib only.
"""
from __future__ import annotations

import os
import re
from typing import Optional


NAME = "gpu_cpu_affinity"


_PCI_ROOT = "/sys/bus/pci/devices"
_CPU_ONLINE = "/sys/devices/system/cpu/online"


_RANGE_RE = re.compile(r"^(\d+)(?:-(\d+))?$")


def parse_cpu_list(s: Optional[str]) -> list:
    if not s:
        return []
    out: list = []
    for tok in s.strip().split(","):
        m = _RANGE_RE.match(tok.strip())
        if not m:
            continue
        a = int(m.group(1))
        b = int(m.group(2)) if m.group(2) else a
        out.extend(range(a, b + 1))
    return sorted(set(out))


def _read(p: str) -> Optional[str]:
    try:
        with open(p) as f:
            return f.read().strip()
    except OSError:
        return None


def find_nvidia_bdfs(pci_root: str = _PCI_ROOT) -> list:
    out: list = []
    try:
        for n in sorted(os.listdir(pci_root)):
            vp = os.path.join(pci_root, n, "vendor")
            cp = os.path.join(pci_root, n, "class")
            try:
                with open(vp) as f:
                    if f.read().strip().lower() != "0x10de":
                        continue
                with open(cp) as f:
                    klass = f.read().strip().lower()
                if klass.startswith("0x03"):
                    out.append(n)
            except OSError:
                continue
    except OSError:
        return []
    return out


def read_local_cpulist(pci_root: str, bdf: str) -> Optional[str]:
    return _read(os.path.join(pci_root, bdf, "local_cpulist"))


def read_numa_node(pci_root: str, bdf: str) -> Optional[int]:
    s = _read(os.path.join(pci_root, bdf, "numa_node"))
    if s is None:
        return None
    try:
        return int(s)
    except ValueError:
        return None


def _read_total_cpus(cpu_online: str) -> int:
    s = _read(cpu_online)
    return len(parse_cpu_list(s)) if s else 0


_RECIPE = (
    "# Pin llama-server to the GPU's local CPUs via systemd Drop-In:\n"
    "sudo mkdir -p /etc/systemd/system/llama-server.service.d\n"
    "sudo tee /etc/systemd/system/llama-server.service.d/cpu_affinity.conf <<'EOF'\n"
    "[Service]\n"
    "CPUAffinity={local_cpulist}\n"
    "EOF\n"
    "sudo systemctl daemon-reload && sudo systemctl restart llama-server\n"
    "# One-shot equivalent:\n"
    "taskset -c {local_cpulist} llama-server --model ...\n"
    "# Complements: #35.3 numa_placement (node level), #31.3 cpu_topology."
)


_RANK = {
    "no_gpus": 0,
    "single_node_affinity": 0,
    "unknown": 1,
    "unset": 1,
    "constrained_affinity": 2,
}


def classify(cards: list, total_cpus: int) -> dict:
    if not cards:
        return {"verdict": "no_gpus",
                "reason": "No NVIDIA VGA devices found.",
                "recommendation": ""}
    worst = "single_node_affinity"
    worst_card = None
    for c in cards:
        local = c.get("local_cpus_count") or 0
        cpulist = c.get("local_cpulist")
        if cpulist is None and c.get("numa_node") is None:
            v = "unset"
        elif local == 0:
            v = "unknown"
        elif total_cpus > 0 and local < total_cpus:
            v = "constrained_affinity"
        else:
            v = "single_node_affinity"
        if _RANK.get(v, 0) > _RANK.get(worst, 0):
            worst = v
            worst_card = c
    if worst == "single_node_affinity":
        return {"verdict": "single_node_affinity",
                "reason": (f"All {total_cpus} online CPUs are local to "
                           f"each GPU — single-socket / single-CCD host, "
                           f"no affinity preference matters."),
                "recommendation": ""}
    if worst == "unset":
        return {"verdict": "unset",
                "reason": ("PCI device(s) have no local_cpulist exposed "
                           "— old kernel, virtual GPU, or exotic VM."),
                "recommendation": ""}
    if worst == "unknown":
        return {"verdict": "unknown",
                "reason": "Could not read local_cpus count.",
                "recommendation": ""}
    # constrained_affinity
    lcl = worst_card.get("local_cpulist") or "?"
    return {
        "verdict": "constrained_affinity",
        "reason": (f"GPU {worst_card['gpu_bdf']} has only "
                   f"{worst_card.get('local_cpus_count')} local CPUs "
                   f"({lcl}) out of {total_cpus} total. Threads "
                   f"running on remote CPUs pay Infinity Fabric / UPI "
                   f"latency on every PCIe DMA."),
        "recommendation": _RECIPE.format(local_cpulist=lcl),
    }


def status(cfg=None) -> dict:
    bdfs = find_nvidia_bdfs(_PCI_ROOT)
    total_cpus = _read_total_cpus(_CPU_ONLINE)
    cards: list = []
    for bdf in bdfs:
        cpulist = read_local_cpulist(_PCI_ROOT, bdf)
        numa_node = read_numa_node(_PCI_ROOT, bdf)
        cards.append({
            "gpu_bdf": bdf,
            "local_cpulist": cpulist,
            "local_cpus_count": len(parse_cpu_list(cpulist)),
            "numa_node": numa_node,
        })
    verdict = classify(cards, total_cpus)
    return {
        "ok": True,
        "gpu_count": len(cards),
        "total_cpus": total_cpus,
        "cards": cards,
        "verdict": verdict,
    }
