"""Module numa_hmat_access_audit — NUMA HMAT/CDAT access-matrix
audit (R&D #76.3).

HMAT (Heterogeneous Memory Attribute Table, ACPI 6.3) and the
companion CDAT for CXL expose per-node performance metadata as
:

  /sys/devices/system/node/node<N>/access<I>/initiators/
  /sys/devices/system/node/node<N>/access<I>/targets/
  /sys/devices/system/node/node<N>/access<I>/{
      read_bandwidth, read_latency,
      write_bandwidth, write_latency
  }

`access0` = closest initiator class, `access1` = second-class.
Bandwidth in MB/s ; latency in nanoseconds.

Why on a homelab :

* On a single-socket consumer board with a P/E hybrid CPU +
  RTX 3090 + a future CXL.mem module, HMAT-reported asymmetric
  bandwidth between initiator CPUs and the memory node hosting
  the model weights is the difference between 50 % and 100 %
  inference throughput — and existing numa_placement (meminfo-
  only) can't catch it.
* A bandwidth cliff (≥ 3× drop) between access0 and access1
  flags GPU passthrough on the wrong NUMA quadrant.

Existing numa_placement_audit reads /proc/self/status numa_maps
counts ; numa_topology_audit reads distance matrices ; neither
parses HMAT bandwidth/latency.

Verdicts (priority order) :
  cross_node_bw_cliff       ≥1 node pair where access1 bandwidth
                              is < access0/3 (≥ 3× cliff).
  asymmetric_latency        access0 latency stddev > 20 % of
                              mean across nodes (one node
                              materially slower).
  hmat_absent               nodes present but no access* dirs
                              exposed (firmware did not publish
                              HMAT/CDAT).
  single_node_uniform       only one NUMA node (no topology to
                              audit — informational).
  ok                        HMAT present and uniform.
  unknown                   /sys/devices/system/node absent.

stdlib only.
"""
from __future__ import annotations

import os
import re
from typing import Dict, List, Optional


NAME = "numa_hmat_access_audit"


_SYS_NODE = "/sys/devices/system/node"


_NODE_RE = re.compile(r"^node(\d+)$")
_ACCESS_RE = re.compile(r"^access(\d+)$")

_CLIFF_FACTOR = 3.0
_ASYM_FRAC = 0.20


def _read(path: str) -> Optional[str]:
    try:
        with open(path) as f:
            return f.read().strip()
    except OSError:
        return None


def _read_int(path: str) -> Optional[int]:
    t = _read(path)
    if t is None:
        return None
    try:
        return int(t)
    except ValueError:
        return None


def list_numa_nodes(sys_node: str = _SYS_NODE) -> List[int]:
    if not os.path.isdir(sys_node):
        return []
    out: List[int] = []
    try:
        for n in sorted(os.listdir(sys_node)):
            m = _NODE_RE.match(n)
            if m:
                out.append(int(m.group(1)))
    except OSError:
        return []
    return out


def read_access(sys_node: str, node_id: int,
                  access_idx: int) -> dict:
    d = os.path.join(sys_node, f"node{node_id}",
                          f"access{access_idx}")
    if not os.path.isdir(d):
        return {"present": False}
    return {
        "present": True,
        "read_bandwidth": _read_int(
            os.path.join(d, "read_bandwidth")),
        "read_latency": _read_int(
            os.path.join(d, "read_latency")),
        "write_bandwidth": _read_int(
            os.path.join(d, "write_bandwidth")),
        "write_latency": _read_int(
            os.path.join(d, "write_latency")),
    }


def list_node_access(sys_node: str = _SYS_NODE) -> dict:
    """Returns {node_id: {access_idx: {...}}}."""
    out: Dict[int, Dict[int, dict]] = {}
    for nid in list_numa_nodes(sys_node):
        out[nid] = {}
        for ai in (0, 1):
            entry = read_access(sys_node, nid, ai)
            if entry.get("present"):
                out[nid][ai] = entry
    return out


def classify(present: bool,
              node_count: int,
              accesses: Dict[int, Dict[int, dict]]) -> dict:
    if not present:
        return {"verdict": "unknown",
                "reason": ("/sys/devices/system/node absent — "
                          "unusual non-NUMA kernel."),
                "recommendation": ""}

    if node_count <= 1:
        return {"verdict": "single_node_uniform",
                "reason": (f"{node_count} NUMA node(s) — "
                          f"informational, no HMAT topology "
                          f"to audit on a single-node host."),
                "recommendation": ""}

    # 1) cross_node_bw_cliff — access1 < access0/3 on any node
    cliff_nodes: List[int] = []
    for nid, acc in accesses.items():
        a0 = acc.get(0, {})
        a1 = acc.get(1, {})
        bw0 = a0.get("read_bandwidth")
        bw1 = a1.get("read_bandwidth")
        if (bw0 is not None and bw1 is not None
                and bw0 > 0 and bw1 < bw0 / _CLIFF_FACTOR):
            cliff_nodes.append(nid)
    if cliff_nodes:
        sample = ", ".join(f"node{n}" for n in cliff_nodes[:3])
        return {"verdict": "cross_node_bw_cliff",
                "reason": (f"{len(cliff_nodes)} NUMA node(s) "
                          f"with access1 read_bandwidth < "
                          f"access0/{_CLIFF_FACTOR:g} : "
                          f"{sample}. CPU↔mem bandwidth cliff."),
                "recommendation": _recipe_bw_cliff()}

    # 2) asymmetric_latency — stddev across nodes > 20% of mean
    access0_lats = [acc.get(0, {}).get("read_latency")
                          for acc in accesses.values()]
    access0_lats = [v for v in access0_lats if v is not None]
    if len(access0_lats) >= 2:
        mean = sum(access0_lats) / len(access0_lats)
        var = sum((v - mean) ** 2 for v in access0_lats) \
            / len(access0_lats)
        std = var ** 0.5
        if mean > 0 and (std / mean) > _ASYM_FRAC:
            return {"verdict": "asymmetric_latency",
                    "reason": (f"access0 read_latency stddev "
                              f"{std:.0f}ns / mean {mean:.0f}ns "
                              f"= {100*std/mean:.0f} % (> "
                              f"{100*_ASYM_FRAC:.0f} %)."),
                    "recommendation": _recipe_asym()}

    # 3) hmat_absent — multi-node but no access dirs
    nodes_with_access = sum(1 for acc in accesses.values()
                                       if acc)
    if nodes_with_access == 0:
        return {"verdict": "hmat_absent",
                "reason": (f"{node_count} NUMA nodes detected "
                          f"but no access<N>/ subdirs found. "
                          f"Firmware did not publish HMAT/CDAT."),
                "recommendation": _recipe_hmat_absent()}

    return {"verdict": "ok",
            "reason": (f"{node_count} NUMA node(s) ; "
                      f"{nodes_with_access} expose HMAT "
                      f"access data ; no cliffs detected."),
            "recommendation": ""}


def status(config=None, sys_node: str = _SYS_NODE) -> dict:
    present = os.path.isdir(sys_node)
    nodes = list_numa_nodes(sys_node) if present else []
    accesses = list_node_access(sys_node) if present else {}
    verdict = classify(present, len(nodes), accesses)
    return {"ok": present,
              "present": present,
              "node_count": len(nodes),
              "nodes": nodes,
              "accesses": {
                  str(nid): {str(ai): v for ai, v in acc.items()}
                      for nid, acc in accesses.items()},
              "has_cpu": _read(os.path.join(
                  sys_node, "has_cpu")) if present else None,
              "has_memory": _read(os.path.join(
                  sys_node, "has_memory")) if present else None,
              "verdict": verdict}


# ── recovery recipes ────────────────────────────────────────────

def _recipe_bw_cliff() -> str:
    return ("# A NUMA node has a > 3x bandwidth cliff between\n"
            "# access0 (closest) and access1 (second-class)\n"
            "# initiator. Bind GPU workloads to access0 :\n"
            "numactl --hardware\n"
            "numactl --cpunodebind=<node> --membind=<node> \\\n"
            "    -- <command>\n"
            "# Verify HMAT placement :\n"
            "for n in /sys/devices/system/node/node*; do\n"
            "  echo \"$n : a0_bw=$(cat $n/access0/read_bandwidth 2>/dev/null)\"\n"
            "done\n")


def _recipe_asym() -> str:
    return ("# Asymmetric NUMA latency across nodes. Use\n"
            "# numactl --localalloc or --interleave for memory-\n"
            "# bound CUDA pre-processing :\n"
            "numactl --interleave=all -- <command>\n"
            "lstopo --no-io | head\n")


def _recipe_hmat_absent() -> str:
    return ("# Multi-node host but no HMAT/CDAT published. On\n"
            "# Linux 5.6+ HMAT is auto-detected if ACPI publishes\n"
            "# the table. Confirm :\n"
            "sudo dmesg | grep -iE 'HMAT|CDAT' | head\n"
            "ls /sys/firmware/acpi/tables/ | grep -i HMAT\n")
