"""Module hybrid_cpu_topo — heterogeneous CPU topology auditor (R&D #42.2).

Shipped #31.3 cpu_topology shows SMT siblings + governor globally,
shipped #37.4 cpu_cache_topology shows L3 islands — but neither
identifies the *cluster* layout that matters most for surgical
inference pinning on modern heterogeneous CPUs :

  Alder/Raptor/Lunar Lake     P-cluster + E-cluster + LP-E cluster
                              ; pin inference to P-cores only.
  Zen3/4/5 multi-CCD          one CCD per die_id ; pin inference to
                              one CCD so the L3 stays hot.
  Snapdragon X Elite          Oryon P-cluster + Oryon LP-cluster.
  Apple-via-Asahi M-series    P-cluster + E-cluster (cluster_id
                              differs by 1 from the P-cluster end).

The kernel exposes :
  /sys/devices/system/cpu/cpu*/topology/cluster_id
  /sys/devices/system/cpu/cpu*/topology/die_id
  /sys/devices/system/cpu/cpu*/topology/physical_package_id
  /sys/devices/system/cpu/cpu*/topology/core_id
  /sys/devices/system/cpu/cpu*/cpufreq/cpuinfo_max_freq  (per-policy,
                              the cleanest "this is a P-core" signal)

Group CPUs by (package, die, cluster) and look at the max-freq
distribution :
  - all same max_freq + single cluster      → uniform_topology
  - 2+ distinct max_freq tiers in one die   → p_e_hybrid (P-cluster
                                                + E-cluster split)
  - same max_freq + 2+ die_ids              → multi_ccd_or_multi_die
  - 2+ clusters with same max_freq          → multi_cluster_uniform
                                                (Snapdragon X
                                                Oryon-only, or
                                                older Zen with
                                                single die per
                                                CCD reporting
                                                cluster_id)

Verdicts :
  uniform_topology              homogeneous flat topology — no
                                surgical pinning advice ; the box
                                is what it is.
  p_e_hybrid                    P/E-cluster split detected by max-
                                freq tier → recipe to pin inference
                                workers to the P-cluster CPUs only.
  multi_ccd_or_multi_die        2+ dies in package → recipe to pin
                                inference to one die so L3 stays
                                hot (cross-ref shipped #35.3
                                numa_placement).
  multi_cluster_uniform         2+ clusters, identical max-freq —
                                pin to the lower-index cluster for
                                cache-locality (typical Zen single-
                                die-per-CCD report).
  qemu_or_masked                cluster_id uniform-per-CPU (each
                                vCPU its own cluster) + cpufreq
                                absent → hypervisor passthrough
                                with no real topology data.
  unknown                       /sys/devices/system/cpu unreadable.

stdlib only.
"""
from __future__ import annotations

import os
import re
from typing import Optional


NAME = "hybrid_cpu_topo"


_SYS_CPU = "/sys/devices/system/cpu"


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
        if re.match(r"^cpu\d+$", name):
            out.append(int(name[3:]))
    out.sort()
    return out


def read_cpu_topology(sys_cpu: str, cpu: int) -> dict:
    base = os.path.join(sys_cpu, f"cpu{cpu}")
    topo = os.path.join(base, "topology")
    cpufreq = os.path.join(base, "cpufreq")
    return {
        "cpu": cpu,
        "package_id": _read_int(
            os.path.join(topo, "physical_package_id")),
        "die_id": _read_int(os.path.join(topo, "die_id")),
        "cluster_id": _read_int(os.path.join(topo, "cluster_id")),
        "core_id": _read_int(os.path.join(topo, "core_id")),
        "max_freq_khz": _read_int(
            os.path.join(cpufreq, "cpuinfo_max_freq")),
    }


def group_by(rows: list, *keys) -> dict:
    out: dict = {}
    for r in rows:
        k = tuple(r.get(k) for k in keys)
        out.setdefault(k, []).append(r)
    return out


def freq_tiers(rows: list) -> list:
    """Return distinct max_freq tiers as a sorted descending list."""
    seen: set = set()
    for r in rows:
        f = r.get("max_freq_khz")
        if isinstance(f, int) and f > 0:
            seen.add(f)
    return sorted(seen, reverse=True)


_RECIPE_PIN_P_CLUSTER = (
    "# P/E hybrid CPU detected. Pin inference workers to the\n"
    "# P-cluster (highest max-freq tier) so the BIG cores run\n"
    "# inference and the small cores absorb the background\n"
    "# (kswapd, ksoftirqd, system services) :\n"
    "# Identify the P-cluster cores from the cluster list below,\n"
    "# then use systemd's CPUAffinity= or taskset/numactl :\n"
    "# Example for ollama systemd unit override :\n"
    "sudo systemctl edit ollama.service\n"
    "# Add :\n"
    "# [Service]\n"
    "# CPUAffinity=<P-cluster CPU list — see card UI>"
)

_RECIPE_PIN_ONE_DIE = (
    "# Multi-CCD / multi-die package detected. Pin inference to\n"
    "# ONE die so the L3 stays hot. Cross-references shipped\n"
    "# #35.3 numa_placement — die_id correlates with NUMA node\n"
    "# on Zen3+ + EPYC. Recipe :\n"
    "sudo systemctl edit ollama.service\n"
    "# Add :\n"
    "# [Service]\n"
    "# CPUAffinity=<die-0 CPU list — see card UI>"
)


def classify(rows: list) -> dict:
    if not rows:
        return {"verdict": "unknown",
                "reason": "/sys/devices/system/cpu unreadable.",
                "recommendation": ""}
    # qemu / minimal hypervisor : each vCPU its own cluster_id,
    # no cpufreq.
    clusters = {r.get("cluster_id") for r in rows}
    has_freq = any(isinstance(r.get("max_freq_khz"), int)
                   and r["max_freq_khz"] > 0 for r in rows)
    n = len(rows)
    if not has_freq and len(clusters) == n:
        return {"verdict": "qemu_or_masked",
                "reason": ("Each CPU reports a distinct cluster_id "
                           "and no cpufreq policy is exposed — "
                           "hypervisor passthrough without real "
                           "topology. Cluster pinning advice is "
                           "not actionable here."),
                "recommendation": ""}
    tiers = freq_tiers(rows)
    dies = {r.get("die_id") for r in rows
              if r.get("die_id") is not None}
    # P/E hybrid : ≥ 2 distinct max-freq tiers in the same package.
    by_pkg = group_by(rows, "package_id")
    p_e_detected = False
    p_cluster_cpus: list = []
    for pkg, group in by_pkg.items():
        local_tiers = freq_tiers(group)
        if len(local_tiers) >= 2:
            p_e_detected = True
            top = local_tiers[0]
            p_cluster_cpus = sorted(r["cpu"] for r in group
                                       if r.get("max_freq_khz") == top)
    if p_e_detected:
        return {"verdict": "p_e_hybrid",
                "reason": (f"Heterogeneous max-freq tiers detected — "
                           f"P-cluster = {len(p_cluster_cpus)} CPU(s) "
                           f"at {tiers[0]/1000:.0f} MHz, "
                           f"E-cluster(s) at "
                           f"{', '.join(f'{t/1000:.0f} MHz' for t in tiers[1:])}. "
                           f"Pin inference workers to "
                           f"CPUAffinity={','.join(str(c) for c in p_cluster_cpus)} "
                           f"to keep the BIG cores hot."),
                "recommendation": _RECIPE_PIN_P_CLUSTER}
    # Multi-die in same package (Zen 3+ CCDs).
    by_pkg_dies: dict = {}
    for pkg, group in by_pkg.items():
        local_dies = {r.get("die_id") for r in group
                        if r.get("die_id") is not None}
        if len(local_dies) >= 2:
            by_pkg_dies[pkg] = local_dies
    if by_pkg_dies:
        # Recommend die-0 CPUs.
        pkg0 = next(iter(by_pkg_dies))
        die0 = sorted(by_pkg_dies[pkg0])[0]
        die0_cpus = sorted(r["cpu"] for r in rows
                              if r.get("package_id") == pkg0
                              and r.get("die_id") == die0)
        return {"verdict": "multi_ccd_or_multi_die",
                "reason": (f"Multi-die package detected (pkg "
                           f"{pkg0} has {len(by_pkg_dies[pkg0])} "
                           f"die_ids). Pin inference to die "
                           f"{die0} : CPUAffinity="
                           f"{','.join(str(c) for c in die0_cpus)} "
                           f"so the L3 stays hot."),
                "recommendation": _RECIPE_PIN_ONE_DIE}
    # 2+ clusters, uniform freq → multi-cluster but homogeneous.
    real_clusters = {c for c in clusters if c is not None}
    if has_freq and len(real_clusters) >= 2 and len(tiers) == 1:
        return {"verdict": "multi_cluster_uniform",
                "reason": (f"{len(real_clusters)} cluster(s) "
                           f"reported with uniform max-freq "
                           f"{tiers[0]/1000:.0f} MHz. Pin to the "
                           f"lower cluster_id for cache locality."),
                "recommendation": _RECIPE_PIN_ONE_DIE}
    return {"verdict": "uniform_topology",
            "reason": (f"Homogeneous topology : "
                       f"{len(rows)} CPU(s), "
                       f"{len(real_clusters) or 1} cluster, "
                       f"{len(dies) or 1} die, "
                       + (f"{tiers[0]/1000:.0f} MHz max"
                            if tiers else "no cpufreq exposed")
                       + " — no surgical pinning advice."),
            "recommendation": ""}


def status(cfg=None) -> dict:
    cpus = list_cpus(_SYS_CPU)
    rows = [read_cpu_topology(_SYS_CPU, c) for c in cpus]
    verdict = classify(rows)
    # Compute a small summary structure for the UI.
    packages = sorted({r["package_id"] for r in rows
                       if r["package_id"] is not None})
    dies = sorted({r["die_id"] for r in rows
                    if r["die_id"] is not None})
    clusters = sorted({r["cluster_id"] for r in rows
                         if r["cluster_id"] is not None})
    tiers = freq_tiers(rows)
    return {
        "ok": bool(cpus),
        "cpu_count": len(cpus),
        "packages": packages,
        "dies": dies,
        "clusters": clusters,
        "freq_tiers_khz": tiers,
        "per_cpu": rows,
        "verdict": verdict,
    }
