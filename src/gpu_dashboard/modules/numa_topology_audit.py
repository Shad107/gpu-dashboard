"""Module numa_topology_audit — NUMA + GPU affinity (R&D #55.2).

Distinct from existing #54.1 hugepages_audit (per-node HugeTLB
counts) — this module surveys :

  /sys/devices/system/node/online                node-id set
  /sys/devices/system/node/node*/{distance,
                                     numastat,
                                     meminfo}
  /sys/devices/system/node/has_{cpu,memory}
  /sys/bus/pci/devices/<gpu-bdf>/{numa_node,
                                     local_cpulist}
  /proc/sys/kernel/numa_balancing
  /sys/kernel/mm/numa/demotion_enabled

Catches the bare-metal LLM placement foot-guns :

* GPU PCIe slot reports numa_node = -1 (BIOS ACPI _PXM is missing
  or wrong) → llama-server allocates KV-cache on whichever DDR
  controller the scheduler picks first, pays cross-socket
  latency on every memcpy.
* numa_balancing=0 on a multi-node host with a non-pinned
  workload — pages never migrate to the local controller.
* numa_hit / (numa_hit + numa_miss) < 95 % on at least one
  node → a real fraction of allocs are crossing the QPI link.

Verdicts (priority-ordered) :
  gpu_numa_unset                    NVIDIA display PCI device(s)
                                    with numa_node = -1 on a
                                    multi-node host.
  cross_node_memory                 ≥1 node has miss_ratio > 5 %
                                    of (hit + miss).
  balancing_off_on_multi_node       ≥2 NUMA nodes AND
                                    /proc/sys/kernel/numa_balancing
                                    = 0.
  single_node                       single NUMA node — nothing to
                                    audit (typical homelab).
  ok                                multi-node host, balanced.
  unknown                           /sys/devices/system/node absent.

stdlib only.
"""
from __future__ import annotations

import os
import re
from typing import Dict, List, Optional


NAME = "numa_topology_audit"


_SYS_NODE = "/sys/devices/system/node"
_PROC_NUMA_BALANCING = "/proc/sys/kernel/numa_balancing"
_SYS_NUMA_MM = "/sys/kernel/mm/numa"
_SYS_PCI = "/sys/bus/pci/devices"

_NVIDIA_VENDOR = "0x10de"
_DISPLAY_BASE_CLASS = 0x03

_NODE_DIR_RE = re.compile(r"^node(\d+)$")


def _read(p: str) -> Optional[str]:
    try:
        with open(p) as f:
            return f.read().strip()
    except OSError:
        return None


def _read_int(p: str) -> Optional[int]:
    t = _read(p)
    if t is None:
        return None
    try:
        return int(t)
    except ValueError:
        return None


def list_nodes(sys_node: str = _SYS_NODE) -> List[dict]:
    if not os.path.isdir(sys_node):
        return []
    out: List[dict] = []
    for name in sorted(os.listdir(sys_node)):
        m = _NODE_DIR_RE.match(name)
        if not m:
            continue
        nid = int(m.group(1))
        d = os.path.join(sys_node, name)
        node = {
            "id": nid,
            "distance": (_read(os.path.join(d, "distance")) or
                            "").split(),
            "cpulist": _read(os.path.join(d, "cpulist")),
        }
        node["numastat"] = parse_numastat(_read(
            os.path.join(d, "numastat")))
        out.append(node)
    return out


def parse_numastat(text: Optional[str]) -> Dict[str, int]:
    """Parse the per-node numastat key/value lines."""
    out: Dict[str, int] = {}
    if not text:
        return out
    for line in text.splitlines():
        parts = line.split()
        if len(parts) < 2 or not parts[1].lstrip("-").isdigit():
            continue
        out[parts[0]] = int(parts[1])
    return out


def list_nvidia_gpus(sys_pci: str = _SYS_PCI) -> List[dict]:
    if not os.path.isdir(sys_pci):
        return []
    out: List[dict] = []
    for bdf in sorted(os.listdir(sys_pci)):
        ddir = os.path.join(sys_pci, bdf)
        vendor = _read(os.path.join(ddir, "vendor"))
        klass = _read(os.path.join(ddir, "class"))
        if vendor != _NVIDIA_VENDOR or not klass:
            continue
        try:
            base = (int(klass, 16) >> 16) & 0xff
        except ValueError:
            continue
        if base != _DISPLAY_BASE_CLASS:
            continue
        out.append({
            "bdf": bdf,
            "numa_node": _read_int(
                os.path.join(ddir, "numa_node")),
            "local_cpulist": _read(
                os.path.join(ddir, "local_cpulist")),
        })
    return out


def classify(nodes: List[dict], numa_balancing: Optional[int],
              gpus: List[dict]) -> dict:
    if not nodes:
        return {"verdict": "unknown",
                "reason": ("/sys/devices/system/node is not "
                          "readable."),
                "recommendation": ""}

    multi_node = len(nodes) >= 2

    # 1) gpu_numa_unset — only meaningful when multi-node AND GPU
    #    present.
    if multi_node and gpus:
        unset = [g for g in gpus if g.get("numa_node") == -1]
        if unset:
            sample = ", ".join(g["bdf"] for g in unset)
            return {"verdict": "gpu_numa_unset",
                    "reason": (f"{len(unset)} NVIDIA GPU(s) have "
                              f"numa_node = -1 on a multi-node "
                              f"host : {sample}. BIOS ACPI _PXM "
                              f"likely missing or wrong."),
                    "recommendation": _recipe_set_gpu_numa(
                        unset[0]["bdf"])}

    # 2) cross_node_memory
    if multi_node:
        bad: List[str] = []
        for n in nodes:
            ns = n.get("numastat") or {}
            hit = ns.get("numa_hit", 0)
            miss = ns.get("numa_miss", 0)
            total = hit + miss
            if total > 100_000 and miss > total * 0.05:
                pct = 100 * miss / total
                bad.append(
                    f"node{n['id']} miss={pct:.1f}%")
        if bad:
            return {"verdict": "cross_node_memory",
                    "reason": (f"NUMA miss ratio > 5 % on : "
                              f"{', '.join(bad[:3])}."),
                    "recommendation": _recipe_pin_workload()}

    # 3) balancing_off_on_multi_node
    if multi_node and numa_balancing == 0:
        return {"verdict": "balancing_off_on_multi_node",
                "reason": ("/proc/sys/kernel/numa_balancing = 0 on "
                          "a multi-NUMA-node host — pages never "
                          "migrate to the local controller."),
                "recommendation": _recipe_enable_balancing()}

    # 4) single_node — informational
    if not multi_node:
        return {"verdict": "single_node",
                "reason": (f"Single NUMA node — nothing to balance. "
                          f"{len(gpus)} NVIDIA GPU(s) detected."),
                "recommendation": ""}

    return {"verdict": "ok",
            "reason": (f"{len(nodes)} NUMA nodes, numa_balancing="
                      f"{numa_balancing}, GPU affinity set."),
            "recommendation": ""}


def status(config=None,
            sys_node: str = _SYS_NODE,
            proc_balancing: str = _PROC_NUMA_BALANCING,
            sys_pci: str = _SYS_PCI) -> dict:
    nodes = list_nodes(sys_node)
    numa_balancing = _read_int(proc_balancing)
    gpus = list_nvidia_gpus(sys_pci)
    ok = bool(nodes)
    verdict = classify(nodes, numa_balancing, gpus)
    return {"ok": ok,
              "node_count": len(nodes),
              "nodes": nodes,
              "numa_balancing": numa_balancing,
              "nvidia_gpus": gpus,
              "verdict": verdict}


# ── recovery recipes ────────────────────────────────────────────

def _recipe_set_gpu_numa(bdf: str) -> str:
    return (f"# Force a NUMA node for the GPU until the BIOS fix\n"
            f"# (root, runtime-only — does NOT persist on reboot) :\n"
            f"echo 0 | sudo tee /sys/bus/pci/devices/{bdf}/numa_node\n"
            f"# Persistent fix : BIOS — set NPS=1 (AMD) / NUMA per\n"
            f"# socket (Intel) ; otherwise pin runtimes :\n"
            f"#   numactl --cpunodebind=0 --membind=0 llama-server …\n")


def _recipe_pin_workload() -> str:
    return ("# Find the chatty node and pin the inference user :\n"
            "for n in /sys/devices/system/node/node*; do\n"
            "  echo \"$n : $(grep numa_miss $n/numastat)\"\n"
            "done\n"
            "# Then : sudo systemctl edit llama-server.service\n"
            "#   [Service]\n"
            "#   NUMAPolicy=bind\n"
            "#   NUMAMask=0\n")


def _recipe_enable_balancing() -> str:
    return ("# Enable kernel NUMA balancing :\n"
            "echo 1 | sudo tee /proc/sys/kernel/numa_balancing\n"
            "# Persist via /etc/sysctl.d/99-numa.conf :\n"
            "#   kernel.numa_balancing = 1\n")
