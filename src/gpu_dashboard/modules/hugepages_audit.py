"""Module hugepages_audit — explicit HugeTLB pool (R&D #54.1).

Distinct from existing ksm_audit (which covers transparent
hugepages + KSM) — this module focuses on the **explicit HugeTLB
pool** (2 MiB / 1 GiB) reserved via /proc/sys/vm/nr_hugepages or
the per-node /sys/devices/system/node/*/hugepages/* interface.

Why this matters on an LLM rig :

* CUDA pinned-memory allocations and DPDK / VFIO consumers ask
  for 2M / 1G hugepages from the explicit pool. Reserving pages
  at boot and then never mapping them silently steals GB from
  the page cache.
* On a multi-socket host, hugepages can be skewed onto one NUMA
  node — the wrong-affinity GPU then walks them across QPI.
* A reserved fixed pool with nr_overcommit_hugepages = 0 is
  rigid : a transient consumer that needs more than the static
  pool gets ENOMEM rather than a kernel best-effort.

Reads :
  /sys/kernel/mm/hugepages/hugepages-<sz>/{nr_hugepages,
                                             free_hugepages,
                                             surplus_hugepages,
                                             resv_hugepages,
                                             nr_overcommit_hugepages}
  /sys/devices/system/node/node*/hugepages/hugepages-*/nr_hugepages
  /proc/meminfo (HugePages_*, Hugepagesize, Hugetlb)
  /proc/sys/vm/{nr_hugepages, nr_overcommit_hugepages}

Verdicts (priority-ordered) :
  reserved_unused           ≥1 pool size has nr > 0 AND
                            free == nr (everything reserved,
                            nothing mapped → wasted memory).
  exhausted                 ≥1 pool size has free < 10 % of nr.
  numa_imbalance            ≥2 NUMA nodes AND one node holds
                            > 80 % of any pool's pages.
  overcommit_disabled       nr_hugepages > 0 AND
                            nr_overcommit_hugepages = 0 — rigid
                            pool, transient consumers will
                            ENOMEM rather than grow.
  ok                        pool either unused or healthy.
  unknown                   /sys/kernel/mm/hugepages absent.

stdlib only.
"""
from __future__ import annotations

import os
import re
from typing import Dict, List, Optional


NAME = "hugepages_audit"


_SYS_HP = "/sys/kernel/mm/hugepages"
_SYS_NODE = "/sys/devices/system/node"
_PROC_MEMINFO = "/proc/meminfo"
_PROC_SYS_VM = "/proc/sys/vm"


_NODE_DIR_RE = re.compile(r"^node(\d+)$")
_HP_DIR_RE = re.compile(r"^hugepages-(\d+)kB$")


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


def list_pool_sizes(sys_hp: str = _SYS_HP) -> List[dict]:
    """Enumerate /sys/kernel/mm/hugepages/hugepages-<sz>/ pools."""
    if not os.path.isdir(sys_hp):
        return []
    out: List[dict] = []
    for name in sorted(os.listdir(sys_hp)):
        m = _HP_DIR_RE.match(name)
        if not m:
            continue
        d = os.path.join(sys_hp, name)
        pool = {
            "size_kb": int(m.group(1)),
            "nr": _read_int(os.path.join(d, "nr_hugepages")),
            "free": _read_int(os.path.join(d, "free_hugepages")),
            "surplus": _read_int(os.path.join(d, "surplus_hugepages")),
            "resv": _read_int(os.path.join(d, "resv_hugepages")),
            "nr_overcommit": _read_int(os.path.join(
                d, "nr_overcommit_hugepages")),
        }
        out.append(pool)
    return out


def list_per_node(sys_node: str = _SYS_NODE) -> Dict[int, Dict[int, int]]:
    """Returns {node_id: {pool_size_kb: nr_hugepages, …}, …}."""
    out: Dict[int, Dict[int, int]] = {}
    if not os.path.isdir(sys_node):
        return out
    for name in sorted(os.listdir(sys_node)):
        m = _NODE_DIR_RE.match(name)
        if not m:
            continue
        node = int(m.group(1))
        hp_dir = os.path.join(sys_node, name, "hugepages")
        if not os.path.isdir(hp_dir):
            continue
        pool_map: Dict[int, int] = {}
        for sub in os.listdir(hp_dir):
            m2 = _HP_DIR_RE.match(sub)
            if not m2:
                continue
            n = _read_int(os.path.join(hp_dir, sub, "nr_hugepages"))
            if n is None:
                continue
            pool_map[int(m2.group(1))] = n
        out[node] = pool_map
    return out


def read_meminfo_hp(proc_meminfo: str = _PROC_MEMINFO) -> dict:
    text = _read(proc_meminfo)
    if not text:
        return {}
    out: dict = {}
    for line in text.splitlines():
        for prefix in ("HugePages_Total:", "HugePages_Free:",
                          "HugePages_Rsvd:", "HugePages_Surp:",
                          "Hugepagesize:", "Hugetlb:",
                          "AnonHugePages:", "ShmemHugePages:",
                          "FileHugePages:"):
            if line.startswith(prefix):
                parts = line.split()
                if len(parts) >= 2 and parts[1].isdigit():
                    out[prefix.rstrip(":").lower()] = int(parts[1])
    return out


def classify(pools: List[dict],
              per_node: Dict[int, Dict[int, int]]) -> dict:
    if not pools:
        return {"verdict": "unknown",
                "reason": "/sys/kernel/mm/hugepages not readable.",
                "recommendation": ""}

    # 1) reserved_unused — pool reserved but nothing mapped
    unused = [p for p in pools
                if (p.get("nr") or 0) > 0 and
                   (p.get("free") or 0) == (p.get("nr") or 0)]
    if unused:
        sample = ", ".join(f"{p['size_kb']}kB×{p['nr']}"
                              for p in unused)
        return {"verdict": "reserved_unused",
                "reason": (f"{len(unused)} hugepage pool(s) "
                          f"reserved but unused : {sample}. "
                          f"Each page is locked out of normal "
                          f"page-cache use."),
                "recommendation": _recipe_release_unused()}

    # 2) exhausted — free < 10 % of nr
    busy = [p for p in pools
              if (p.get("nr") or 0) > 0 and
                 (p.get("free") or 0) < (p["nr"] * 0.10)]
    if busy:
        sample = ", ".join(f"{p['size_kb']}kB free={p['free']}/{p['nr']}"
                              for p in busy)
        return {"verdict": "exhausted",
                "reason": (f"{len(busy)} hugepage pool(s) nearly "
                          f"exhausted : {sample}."),
                "recommendation": _recipe_grow_pool()}

    # 3) numa_imbalance — >2 nodes AND one holds >80 % for some size
    if len(per_node) >= 2:
        for size in {sz for m in per_node.values() for sz in m}:
            totals = {n: per_node[n].get(size, 0)
                        for n in per_node}
            grand = sum(totals.values())
            if grand > 0:
                hot = max(totals.values())
                if hot > grand * 0.80:
                    biggest_node = max(totals,
                                          key=lambda k: totals[k])
                    return {"verdict": "numa_imbalance",
                            "reason": (f"{size} kB pool on node"
                                      f"{biggest_node} holds "
                                      f"{hot}/{grand} pages "
                                      f"(> 80 %)."),
                            "recommendation":
                                _recipe_numa_rebalance(size)}

    # 4) overcommit_disabled
    rigid = [p for p in pools
                if (p.get("nr") or 0) > 0 and
                   (p.get("nr_overcommit") or 0) == 0]
    if rigid:
        return {"verdict": "overcommit_disabled",
                "reason": (f"{len(rigid)} pool(s) have a fixed "
                          f"reservation with nr_overcommit_hugepages "
                          f"= 0. Transient consumers will ENOMEM."),
                "recommendation": _recipe_enable_overcommit()}

    return {"verdict": "ok",
            "reason": ("Hugepage pools either unused or healthy."),
            "recommendation": ""}


def status(config=None,
            sys_hp: str = _SYS_HP,
            sys_node: str = _SYS_NODE,
            proc_meminfo: str = _PROC_MEMINFO,
            proc_sys_vm: str = _PROC_SYS_VM) -> dict:
    pools = list_pool_sizes(sys_hp)
    per_node = list_per_node(sys_node)
    meminfo = read_meminfo_hp(proc_meminfo)
    nr_hp = _read_int(os.path.join(proc_sys_vm, "nr_hugepages"))
    nr_oc = _read_int(os.path.join(proc_sys_vm,
                                        "nr_overcommit_hugepages"))
    ok = bool(pools)
    verdict = classify(pools, per_node)
    return {"ok": ok,
              "pools": pools,
              "per_node": per_node,
              "meminfo": meminfo,
              "vm_nr_hugepages": nr_hp,
              "vm_nr_overcommit_hugepages": nr_oc,
              "verdict": verdict}


# ── recovery recipes ────────────────────────────────────────────

def _recipe_release_unused() -> str:
    return ("# Release the unused pool back to normal memory :\n"
            "echo 0 | sudo tee /proc/sys/vm/nr_hugepages\n"
            "# … or, for a 1 GiB pool :\n"
            "echo 0 | sudo tee /sys/kernel/mm/hugepages/hugepages-1048576kB/nr_hugepages\n"
            "# Verify : grep -E 'HugePages|Hugetlb' /proc/meminfo\n")


def _recipe_grow_pool() -> str:
    return ("# Grow the pool — example sets 4096 × 2 MiB :\n"
            "echo 4096 | sudo tee /proc/sys/vm/nr_hugepages\n"
            "# Then verify the consumer sees them :\n"
            "cat /proc/meminfo | grep HugePages\n"
            "# Note : on a fragmented host the kernel may refuse;\n"
            "# try a reboot with hugepages= on cmdline.\n")


def _recipe_numa_rebalance(size_kb: int) -> str:
    return (f"# Rebalance the {size_kb} kB pool across NUMA nodes :\n"
            f"for n in /sys/devices/system/node/node*; do\n"
            f"  echo 1024 | sudo tee $n/hugepages/hugepages-{size_kb}kB/nr_hugepages\n"
            f"done\n"
            f"# Adjust 1024 per node to match your CPU/GPU affinity.\n")


def _recipe_enable_overcommit() -> str:
    return ("# Allow transient overcommit so consumers don't ENOMEM :\n"
            "echo 512 | sudo tee /proc/sys/vm/nr_overcommit_hugepages\n"
            "# Persist via /etc/sysctl.d/99-llm.conf :\n"
            "#   vm.nr_overcommit_hugepages = 512\n")
