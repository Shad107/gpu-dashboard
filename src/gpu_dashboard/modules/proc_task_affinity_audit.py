"""Module proc_task_affinity_audit — task affinity vs GPU (R&D #62.4).

Reads /proc/<pid>/status fields Cpus_allowed_list,
Mems_allowed_list, voluntary_ctxt_switches,
nonvoluntary_ctxt_switches for the dashboard daemon AND every
discoverable LLM-runtime PID, then cross-references with
/sys/bus/pci/devices/<gpu>/local_cpulist.

Distinct from R&D #55.2 numa_topology_audit (which checks GPU
numa_node + per-node numastat) and from any proc_sched module
(which reads /proc/<pid>/sched scheduler stats). This focuses on
the *affinity mask* and its alignment with GPU local CPUs.

Why this matters on a multi-NUMA-node LLM host :

* A container or systemd slice silently narrows Cpus_allowed to
  a CPU set on a different NUMA node than the GPU's PCIe root —
  every H2D copy crosses QPI/IF, throughput halves, and
  `nvidia-smi` still shows full util.
* Mems_allowed restricted to a remote node forces every
  allocation across NUMA.
* High nonvoluntary_ctxt_switches on a narrow cpuset = the
  workload contends with neighbors for those CPUs.

Verdicts (priority-ordered) :
  affinity_excludes_local_numa  ≥1 LLM PID whose Cpus_allowed
                                doesn't overlap with the GPU's
                                local_cpulist.
  mems_allowed_remote_only      ≥1 PID with Mems_allowed_list
                                lacking the GPU's numa_node.
  narrow_cpuset_high_nvcs       ≥1 PID with < 4 allowed CPUs AND
                                nonvoluntary_ctxt_switches > 10000.
  ok                            affinities align with GPU.
  unknown                       no PIDs discoverable.

stdlib only.
"""
from __future__ import annotations

import os
import re
from typing import List, Optional, Set


NAME = "proc_task_affinity_audit"


_PROC = "/proc"
_SYS_BUS_PCI = "/sys/bus/pci/devices"

_NVIDIA_VENDOR = "0x10de"
_DISPLAY_BASE_CLASS = 0x03

_LLM_PROC_PREFIXES = (
    "llama", "vllm", "ollama", "mlc-llm", "mlc_llm",
    "sglang", "aphrodite", "text-generation",
)


def _read(p: str) -> Optional[str]:
    try:
        with open(p) as f:
            return f.read()
    except OSError:
        return None


def parse_cpu_list(s: Optional[str]) -> Set[int]:
    """Parse '0-3,8-11' / '0-11' / '0' into a set of CPU IDs."""
    out: Set[int] = set()
    if not s:
        return out
    for token in s.split(","):
        token = token.strip()
        if not token:
            continue
        if "-" in token:
            lo, _, hi = token.partition("-")
            try:
                out.update(range(int(lo), int(hi) + 1))
            except ValueError:
                continue
        else:
            try:
                out.add(int(token))
            except ValueError:
                continue
    return out


def parse_status(text: Optional[str]) -> dict:
    out: dict = {"Cpus_allowed_list": None,
                   "Mems_allowed_list": None,
                   "voluntary_ctxt_switches": None,
                   "nonvoluntary_ctxt_switches": None}
    if not text:
        return out
    for line in text.splitlines():
        for k in out:
            prefix = k + ":"
            if line.startswith(prefix):
                val = line[len(prefix):].strip()
                if k.endswith("_switches"):
                    try:
                        out[k] = int(val)
                    except ValueError:
                        out[k] = None
                else:
                    out[k] = val
                break
    return out


def find_nvidia_gpus(sys_pci: str = _SYS_BUS_PCI) -> List[dict]:
    if not os.path.isdir(sys_pci):
        return []
    out: List[dict] = []
    for bdf in sorted(os.listdir(sys_pci)):
        ddir = os.path.join(sys_pci, bdf)
        vendor = _read(os.path.join(ddir, "vendor"))
        klass = _read(os.path.join(ddir, "class"))
        if not vendor or not klass:
            continue
        if vendor.strip() != _NVIDIA_VENDOR:
            continue
        try:
            base = (int(klass.strip(), 16) >> 16) & 0xff
        except ValueError:
            continue
        if base != _DISPLAY_BASE_CLASS:
            continue
        local_cpulist = _read(os.path.join(ddir, "local_cpulist"))
        numa = _read(os.path.join(ddir, "numa_node"))
        try:
            numa_node = int(numa.strip()) if numa else None
        except ValueError:
            numa_node = None
        out.append({
            "bdf": bdf,
            "local_cpulist": (local_cpulist or "").strip(),
            "numa_node": numa_node,
            "local_cpus": parse_cpu_list(local_cpulist or ""),
        })
    return out


def find_llm_processes(proc: str = _PROC) -> List[dict]:
    out: List[dict] = []
    if not os.path.isdir(proc):
        return out
    # Self first
    self_status = _read(os.path.join(proc, "self", "status"))
    if self_status:
        out.append({"pid": os.getpid(), "comm": "self",
                      "status": parse_status(self_status)})

    for name in os.listdir(proc):
        if not name.isdigit():
            continue
        comm_text = _read(os.path.join(proc, name, "comm"))
        if not comm_text:
            continue
        c = comm_text.strip().lower()
        if not any(c.startswith(p) for p in _LLM_PROC_PREFIXES):
            continue
        st_text = _read(os.path.join(proc, name, "status"))
        if not st_text:
            continue
        out.append({"pid": int(name),
                      "comm": comm_text.strip(),
                      "status": parse_status(st_text)})
    return sorted(out, key=lambda x: x["pid"])


def classify(candidates: List[dict],
              gpus: List[dict]) -> dict:
    if not candidates:
        return {"verdict": "unknown",
                "reason": ("No /proc/<pid>/status readable for any "
                          "LLM candidate."),
                "recommendation": ""}

    # 1) affinity_excludes_local_numa
    if gpus:
        gpu_cpus = set()
        for g in gpus:
            gpu_cpus.update(g["local_cpus"])
        if gpu_cpus:  # only meaningful when GPU has a cpulist
            bad = []
            for c in candidates:
                allowed = parse_cpu_list(
                    c["status"].get("Cpus_allowed_list"))
                if allowed and not (allowed & gpu_cpus):
                    bad.append(
                        f"{c['comm']}(pid={c['pid']})="
                        f"{c['status'].get('Cpus_allowed_list')}")
            if bad:
                return {"verdict":
                            "affinity_excludes_local_numa",
                        "reason": (f"{len(bad)} LLM PID(s) pinned "
                                  f"to CPUs not in GPU local set "
                                  f"({sorted(gpu_cpus)[:6]}...) : "
                                  f"{bad[0]}. H2D copies cross "
                                  f"QPI."),
                        "recommendation": _recipe_pin_local(
                            gpus[0]["local_cpulist"])}

    # 2) mems_allowed_remote_only
    if gpus:
        gpu_nodes = {g["numa_node"] for g in gpus
                        if g.get("numa_node") is not None and
                           g["numa_node"] >= 0}
        if gpu_nodes:
            bad = []
            for c in candidates:
                mems = parse_cpu_list(
                    c["status"].get("Mems_allowed_list"))
                if mems and not (mems & gpu_nodes):
                    bad.append(
                        f"{c['comm']}(pid={c['pid']})="
                        f"{c['status'].get('Mems_allowed_list')}")
            if bad:
                return {"verdict": "mems_allowed_remote_only",
                        "reason": (f"{len(bad)} LLM PID(s) with "
                                  f"Mems_allowed on remote nodes : "
                                  f"{bad[0]}."),
                        "recommendation": _recipe_membind()}

    # 3) narrow_cpuset_high_nvcs — < 4 allowed CPUs + high nvcs
    bad = []
    for c in candidates:
        allowed = parse_cpu_list(
            c["status"].get("Cpus_allowed_list"))
        nvcs = c["status"].get("nonvoluntary_ctxt_switches")
        if (allowed and 1 <= len(allowed) < 4 and
                nvcs is not None and nvcs > 10000):
            bad.append(
                f"{c['comm']}(pid={c['pid']})={len(allowed)} CPUs "
                f"nvcs={nvcs}")
    if bad:
        return {"verdict": "narrow_cpuset_high_nvcs",
                "reason": (f"{len(bad)} LLM PID(s) with narrow "
                          f"cpuset AND high non-voluntary ctxt "
                          f"switches : {bad[0]}. CPU contention "
                          f"with neighbors."),
                "recommendation": _recipe_widen_cpuset()}

    return {"verdict": "ok",
            "reason": (f"{len(candidates)} candidate(s) — "
                      f"affinities align with GPU."),
            "recommendation": ""}


def status(config=None, proc: str = _PROC,
            sys_bus_pci: str = _SYS_BUS_PCI) -> dict:
    candidates = find_llm_processes(proc)
    gpus = find_nvidia_gpus(sys_bus_pci)
    ok = bool(candidates)
    verdict = classify(candidates, gpus)
    return {"ok": ok,
              "candidate_count": len(candidates),
              "candidates": [
                  {"pid": c["pid"], "comm": c["comm"],
                   "Cpus_allowed_list":
                       c["status"].get("Cpus_allowed_list"),
                   "Mems_allowed_list":
                       c["status"].get("Mems_allowed_list"),
                   "voluntary_ctxt_switches":
                       c["status"].get("voluntary_ctxt_switches"),
                   "nonvoluntary_ctxt_switches":
                       c["status"].get(
                           "nonvoluntary_ctxt_switches")}
                  for c in candidates],
              "gpu_count": len(gpus),
              "gpus": [
                  {"bdf": g["bdf"],
                   "local_cpulist": g["local_cpulist"],
                   "numa_node": g["numa_node"]}
                  for g in gpus],
              "verdict": verdict}


# ── recovery recipes ────────────────────────────────────────────

def _recipe_pin_local(local_cpulist: str) -> str:
    return (f"# Pin the LLM service to the GPU's local CPUs :\n"
            f"# Add to the systemd unit override :\n"
            f"#   [Service]\n"
            f"#   CPUAffinity={local_cpulist}\n"
            f"# Or at command line :\n"
            f"#   taskset -c {local_cpulist} llama-server ...\n"
            f"# Verify with : grep Cpus_allowed_list /proc/<pid>/status\n")


def _recipe_membind() -> str:
    return ("# Bind memory to the GPU's local NUMA node :\n"
            "#   numactl --membind=<node> --cpunodebind=<node> ...\n"
            "# Or systemd unit :\n"
            "#   [Service]\n"
            "#   NUMAPolicy=bind\n"
            "#   NUMAMask=<node>\n")


def _recipe_widen_cpuset() -> str:
    return ("# Widen the cpuset OR move the inference unit to a\n"
            "# dedicated slice :\n"
            "sudo systemctl edit <your-llm>.service\n"
            "#   [Service]\n"
            "#   AllowedCPUs=0-11   # or whatever matches the GPU\n"
            "# … and review cgroup neighbors :\n"
            "find /sys/fs/cgroup -name cpu.weight -exec grep -H . {} \\; | head\n")
