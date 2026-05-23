"""Module numa_placement — NUMA placement auditor (R&D #35.3).

On a dual-socket or NPS4-AMD host, an inference daemon's mmap +
KV-cache can end up split across NUMA nodes — half on node 0,
half on node 1. Cross-node memory reads cost 1.5-2× latency, and
because llama.cpp's prompt-processing inner loop touches the same
weight tensors repeatedly, the cost compounds.

This module enumerates /sys/devices/system/node/node*/{cpulist,
meminfo,distance}, parses /proc/<pid>/numa_maps for each LLM
daemon (counts pages per N<n>= token), and classifies the placement:

  single_node          only one NUMA node — trivially ok
  balanced             multi-node, daemon memory >= 80% on one node
  cross_node_split     daemon memory split across nodes (worst node
                       carries < 80%) — surfaces numactl --membind
                       recipe + systemd NUMAPolicy= Drop-In
  unknown              cannot read sysfs / no data

Recipe options:
  - One-shot: numactl --cpunodebind=N --membind=N llama-server ...
  - Permanent: systemd Drop-In with NUMAPolicy=bind + NUMAMask=N

stdlib only.
"""
from __future__ import annotations

import os
import re
from typing import Optional


NAME = "numa_placement"


_NODE_ROOT = "/sys/devices/system/node"
_PROC = "/proc"


LLM_COMM_PATTERNS = (
    "ollama", "llama-server", "llama_server", "llama.cpp", "llamacpp",
    "vllm", "sglang", "exllamav2", "exllama", "comfyui",
)
LLM_CMDLINE_HINTS = (
    "llama_cpp", "vllm.entrypoints", "ollama", "exllama",
    "text-generation-webui", "comfyui",
)


def _read(p: str) -> Optional[str]:
    try:
        with open(p) as f:
            return f.read()
    except OSError:
        return None


def read_comm(pid: int, proc_root: str = _PROC) -> str:
    t = _read(os.path.join(proc_root, str(pid), "comm"))
    return t.strip() if t else ""


def read_cmdline(pid: int, proc_root: str = _PROC) -> str:
    try:
        with open(os.path.join(proc_root, str(pid), "cmdline"), "rb") as f:
            return f.read().replace(b"\x00", b" ").decode("utf-8",
                                                            errors="replace")
    except OSError:
        return ""


def is_llm_proc(comm: str, cmdline: str) -> bool:
    low = comm.lower()
    for pat in LLM_COMM_PATTERNS:
        if pat in low:
            return True
    if low.startswith("python") or low.startswith("uvicorn"):
        for h in LLM_CMDLINE_HINTS:
            if h in cmdline:
                return True
    return False


_NODE_RE = re.compile(r"^node(\d+)$")


def list_nodes(root: str = _NODE_ROOT) -> list:
    try:
        names = os.listdir(root)
    except OSError:
        return []
    out: list = []
    for n in names:
        m = _NODE_RE.match(n)
        if m and os.path.isdir(os.path.join(root, n)):
            out.append(int(m.group(1)))
    return sorted(out)


def read_node_cpulist(root: str, node_id: int) -> Optional[str]:
    s = _read(os.path.join(root, f"node{node_id}", "cpulist"))
    return s.strip() if s else None


def read_node_distance(root: str, node_id: int) -> list:
    s = _read(os.path.join(root, f"node{node_id}", "distance"))
    if not s:
        return []
    try:
        return [int(x) for x in s.split()]
    except ValueError:
        return []


_MEMINFO_RE = re.compile(r"^Node\s+\d+\s+(\S+):\s+(\d+)\s+kB", re.MULTILINE)


def read_node_meminfo(root: str, node_id: int) -> dict:
    s = _read(os.path.join(root, f"node{node_id}", "meminfo"))
    if not s:
        return {}
    out: dict = {}
    for m in _MEMINFO_RE.finditer(s):
        out[f"{m.group(1)}_kB"] = int(m.group(2))
    return out


_NUMA_PAGES_RE = re.compile(r"\bN(\d+)=(\d+)\b")


def parse_numa_maps(text: str) -> dict:
    """Return {node_id: total_pages} across all VMAs."""
    out: dict = {}
    if not text:
        return out
    for m in _NUMA_PAGES_RE.finditer(text):
        node_id = int(m.group(1))
        pages = int(m.group(2))
        out[node_id] = out.get(node_id, 0) + pages
    return out


_BALANCED_RATIO = 0.80   # >= 80% on one node → balanced


def classify(node_count: int, pid_counts: list) -> dict:
    if node_count <= 0:
        return {"verdict": "unknown",
                "reason": "No NUMA nodes detected.",
                "recommendation": ""}
    if node_count == 1:
        return {"verdict": "single_node",
                "reason": ("Host has a single NUMA node — no placement "
                           "concerns."),
                "recommendation": ""}
    # Multi-node: check whether any LLM proc is split
    worst_split = None
    worst_ratio = 1.0
    for p in pid_counts:
        per_node = p.get("per_node") or {}
        total = sum(per_node.values())
        if total == 0:
            continue
        max_share = max(per_node.values()) / total
        if max_share < worst_ratio:
            worst_ratio = max_share
            worst_split = p
    if worst_split is None:
        return {"verdict": "balanced",
                "reason": (f"{node_count} NUMA nodes, no LLM-daemon "
                           f"placement data to evaluate (proc had no "
                           f"numa_maps pages)."),
                "recommendation": ""}
    if worst_ratio >= _BALANCED_RATIO:
        return {"verdict": "balanced",
                "reason": (f"{node_count} NUMA nodes, daemon "
                           f"`{worst_split['comm']}` (pid "
                           f"{worst_split['pid']}) has {worst_ratio*100:.0f}% "
                           f"of pages on one node — balanced."),
                "recommendation": ""}
    return {
        "verdict": "cross_node_split",
        "reason": (f"Daemon `{worst_split['comm']}` (pid "
                   f"{worst_split['pid']}) has only "
                   f"{worst_ratio*100:.0f}% of pages on its busiest "
                   f"NUMA node — the rest cost 1.5-2× cross-node "
                   f"latency on every weight read."),
        "recommendation": _recipe(worst_split),
    }


def _recipe(worst: dict) -> str:
    per_node = worst.get("per_node") or {}
    if not per_node:
        return ""
    home = max(per_node, key=per_node.get)
    return (
        f"# One-shot launch pinned to node {home}:\n"
        f"numactl --cpunodebind={home} --membind={home} {worst['comm']} ...\n\n"
        f"# Permanent via systemd Drop-In:\n"
        f"sudo mkdir -p /etc/systemd/system/{worst['comm']}.service.d\n"
        f"sudo tee /etc/systemd/system/{worst['comm']}.service.d/numa.conf <<'EOF'\n"
        f"[Service]\n"
        f"NUMAPolicy=bind\n"
        f"NUMAMask={home}\n"
        f"EOF\n"
        f"sudo systemctl daemon-reload && "
        f"sudo systemctl restart {worst['comm']}.service"
    )


def scan_llm_procs(proc_root: str = _PROC) -> list:
    out: list = []
    try:
        names = os.listdir(proc_root)
    except OSError:
        return out
    for n in names:
        if not n.isdigit():
            continue
        pid = int(n)
        comm = read_comm(pid, proc_root)
        cmdline = read_cmdline(pid, proc_root)
        if not is_llm_proc(comm, cmdline):
            continue
        nm = _read(os.path.join(proc_root, str(pid), "numa_maps")) or ""
        per_node = parse_numa_maps(nm)
        out.append({"pid": pid, "comm": comm,
                     "cmdline_short": cmdline[:140],
                     "per_node": per_node})
    return out


def status(cfg=None) -> dict:
    if not os.path.isdir(_NODE_ROOT):
        return {"ok": False, "error": "numa_unavailable",
                "reason": f"{_NODE_ROOT} not present."}
    nodes = list_nodes(_NODE_ROOT)
    node_summary: list = []
    for nid in nodes:
        info = read_node_meminfo(_NODE_ROOT, nid)
        node_summary.append({
            "id": nid,
            "cpu_list": read_node_cpulist(_NODE_ROOT, nid),
            "distance": read_node_distance(_NODE_ROOT, nid),
            "mem_total_kb": info.get("MemTotal_kB"),
            "mem_free_kb": info.get("MemFree_kB"),
        })
    pid_counts = scan_llm_procs(_PROC)
    verdict = classify(len(nodes), pid_counts)
    return {
        "ok": True,
        "node_count": len(nodes),
        "nodes": node_summary,
        "process_count": len(pid_counts),
        "processes": pid_counts,
        "verdict": verdict,
        "worst_verdict": verdict["verdict"],
    }
