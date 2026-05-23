"""Module vm_tuning_deep — page-cache reclaim auditor (R&D #40.3).

Shipped `vm_sysctl_audit` (#32.4) already covers the four heavy-traffic
/proc/sys/vm knobs : swappiness, dirty_ratio, dirty_background_ratio,
overcommit_memory. That leaves the less-commonly-touched but higher-
impact-for-LLM knobs unaudited :

  page-cluster              default 3 = 8-page swap-readahead bursts.
                            On NVMe swap with a 70B-quant paging
                            random tensor blobs, optimal is 0 — single
                            page reads, no readahead waste.
  watermark_scale_factor    default 10 = wake kswapd at 0.1 % free
                            of zone. On a tight 32-GB box loading a
                            24-GB quant, kswapd wakes too late and
                            direct-reclaim stalls inference for 50-
                            200 ms. Bump to 100-500 for early wake.
  vfs_cache_pressure        default 100. A model-loading workload
                            mmaps a few gigabytes of GGUF and barely
                            touches the rest of the FS ; drop to 50
                            so dentry/inode cache for the GGUF survives
                            longer (first-prompt-after-idle stall).
  min_free_kbytes           watermark floor. On a memory-tight rig the
                            default sometimes triggers OOM on transient
                            spikes ; ~256-512 MB is safer.
  zone_reclaim_mode         0 on most boxes, but BIOS-default 1 on some
                            servers — reactivates aggressive same-node
                            reclaim that fights shipped #35.3
                            numa_placement's cross-node intent.
  drop_caches               write-only. Surface its existence as a
                            manual eviction tool ("click to drop pcache
                            before benchmark") with the *warning* that
                            it stalls disk operations.

Verdicts :
  defaults_on_tight_box     all 4 tuning candidates at defaults on a
                            box with < 64 GB RAM ; recommend the
                            paste-ready sysctl.d snippet.
  nvme_swap_readahead_waste page-cluster ≥ 2 + active /proc/swaps
                            entry → flag the readahead waste.
  zone_reclaim_conflict     zone_reclaim_mode=1 — fights numa.
  late_kswapd_wake          watermark_scale_factor ≤ 10 + RAM > 80 %
                            used → kswapd wakes too late.
  ok                        already tuned (≥1 knob away from default).
  unknown                   /proc/sys/vm unreadable.

stdlib only.
"""
from __future__ import annotations

import os
from typing import Optional


NAME = "vm_tuning_deep"


_PROC_SYS_VM = "/proc/sys/vm"
_PROC_SWAPS = "/proc/swaps"
_MEMINFO = "/proc/meminfo"


_FIELDS_INT = (
    "page-cluster", "watermark_scale_factor", "vfs_cache_pressure",
    "min_free_kbytes", "zone_reclaim_mode",
)


_DEFAULTS = {
    "page-cluster": 3,
    "watermark_scale_factor": 10,
    "vfs_cache_pressure": 100,
    "zone_reclaim_mode": 0,
}


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


def read_knobs(sysvm: str = _PROC_SYS_VM) -> dict:
    out: dict = {}
    for f in _FIELDS_INT:
        v = _read_int(os.path.join(sysvm, f))
        if v is not None:
            out[f] = v
    return out


def read_swap_active(proc_swaps: str = _PROC_SWAPS) -> bool:
    text = _read(proc_swaps) or ""
    # Skip header line ; any subsequent line = active swap entry.
    lines = [l for l in text.splitlines() if l.strip()]
    return len(lines) >= 2


def read_meminfo(meminfo_path: str = _MEMINFO) -> dict:
    out: dict = {}
    text = _read(meminfo_path) or ""
    for line in text.splitlines():
        if ":" not in line:
            continue
        k, _, v = line.partition(":")
        v = v.strip()
        # Values are like "32115212 kB" — keep numeric (kB).
        parts = v.split()
        if parts and parts[0].isdigit():
            out[k.strip()] = int(parts[0])
    return out


def mem_pressure(meminfo: dict) -> Optional[float]:
    total = meminfo.get("MemTotal") or 0
    avail = meminfo.get("MemAvailable")
    if not total or avail is None:
        return None
    used = total - avail
    return used / total


_RECIPE_FULL = (
    "# Persistent tuning for a memory-tight LLM rig (32-GB host,\n"
    "# > 20 GB quant resident). Drop into a sysctl.d snippet :\n"
    "sudo tee /etc/sysctl.d/99-llm-vm-tuning.conf <<'EOF'\n"
    "# Single-page swap reads — random tensor blobs gain nothing\n"
    "# from readahead.\n"
    "vm.page-cluster = 0\n"
    "# Wake kswapd early (at 2 % free) — prevent direct-reclaim\n"
    "# inference stalls.\n"
    "vm.watermark_scale_factor = 200\n"
    "# Keep GGUF inode/dentry cache hot.\n"
    "vm.vfs_cache_pressure = 50\n"
    "# Safety floor against transient OOM spikes.\n"
    "vm.min_free_kbytes = 262144\n"
    "EOF\n"
    "sudo sysctl --system"
)

_RECIPE_PAGE_CLUSTER = (
    "# Active NVMe swap + page-cluster=3 (8-page readahead) wastes\n"
    "# IO on random tensor blobs. Single-page reads :\n"
    "echo 0 | sudo tee /proc/sys/vm/page-cluster\n"
    "# Persist :\n"
    "echo 'vm.page-cluster = 0' | \\\n"
    "  sudo tee /etc/sysctl.d/99-llm-page-cluster.conf"
)

_RECIPE_ZONE_RECLAIM = (
    "# zone_reclaim_mode=1 fights cross-NUMA placement\n"
    "# (R&D #35.3 numa_placement). Disable :\n"
    "echo 0 | sudo tee /proc/sys/vm/zone_reclaim_mode\n"
    "echo 'vm.zone_reclaim_mode = 0' | \\\n"
    "  sudo tee /etc/sysctl.d/99-zone-reclaim.conf"
)

_RECIPE_KSWAPD = (
    "# kswapd wakes too late on a tight rig — bump the watermark\n"
    "# scale factor so it wakes at ~2 % free instead of 0.1 % :\n"
    "echo 200 | sudo tee /proc/sys/vm/watermark_scale_factor\n"
    "echo 'vm.watermark_scale_factor = 200' | \\\n"
    "  sudo tee /etc/sysctl.d/99-watermark.conf"
)


def classify(knobs: dict, swap_active: bool,
              meminfo: dict) -> dict:
    if not knobs:
        return {"verdict": "unknown",
                "reason": "/proc/sys/vm knobs unreadable.",
                "recommendation": ""}
    # 1) zone_reclaim conflict is the highest-priority correctness
    #    issue (fights numa_placement) — flag first.
    if knobs.get("zone_reclaim_mode", 0) >= 1:
        return {"verdict": "zone_reclaim_conflict",
                "reason": (f"vm.zone_reclaim_mode="
                           f"{knobs['zone_reclaim_mode']} — kernel "
                           f"will aggressively reclaim within the "
                           f"local NUMA node before spilling to a "
                           f"peer. Defeats numa_placement advice."),
                "recommendation": _RECIPE_ZONE_RECLAIM}
    # 2) NVMe swap + page-cluster default → wasted readahead.
    if swap_active and knobs.get("page-cluster", 3) >= 2:
        return {"verdict": "nvme_swap_readahead_waste",
                "reason": (f"Active swap + vm.page-cluster="
                           f"{knobs['page-cluster']} (default 3 = "
                           f"8-page bursts). Random tensor blobs "
                           f"gain nothing from readahead — every "
                           f"swap-in pulls 7 wasted pages."),
                "recommendation": _RECIPE_PAGE_CLUSTER}
    # 3) Late kswapd on a hot rig.
    pressure = mem_pressure(meminfo)
    if (pressure is not None
            and pressure > 0.80
            and knobs.get("watermark_scale_factor", 10) <= 10):
        pct = round(pressure * 100, 1)
        return {"verdict": "late_kswapd_wake",
                "reason": (f"RAM utilization {pct} % + "
                           f"vm.watermark_scale_factor=10 — kswapd "
                           f"won't wake until free memory drops "
                           f"below 0.1 % of zone. Direct-reclaim "
                           f"stalls inference for 50-200 ms."),
                "recommendation": _RECIPE_KSWAPD}
    # 4) All-defaults across the board on a tight box.
    all_default = all(
        knobs.get(k, _DEFAULTS[k]) == _DEFAULTS[k]
        for k in _DEFAULTS
    )
    mem_total_gb = (meminfo.get("MemTotal") or 0) / (1024 * 1024)
    if all_default and 0 < mem_total_gb <= 48:
        return {"verdict": "defaults_on_tight_box",
                "reason": (f"All 4 deep-tuning knobs at defaults on a "
                           f"{mem_total_gb:.0f}-GB host. Worth "
                           f"applying the LLM-rig sysctl.d snippet."),
                "recommendation": _RECIPE_FULL}
    return {"verdict": "ok",
            "reason": ("≥1 deep-tuning knob already deviates from "
                       "default — assuming it's intentional."),
            "recommendation": ""}


def status(cfg=None) -> dict:
    knobs = read_knobs(_PROC_SYS_VM)
    swap_active = read_swap_active(_PROC_SWAPS)
    meminfo = read_meminfo(_MEMINFO)
    pressure = mem_pressure(meminfo)
    verdict = classify(knobs, swap_active, meminfo)
    return {
        "ok": bool(knobs),
        "knobs": knobs,
        "swap_active": swap_active,
        "mem_total_kb": meminfo.get("MemTotal"),
        "mem_available_kb": meminfo.get("MemAvailable"),
        "mem_pressure": pressure,
        "verdict": verdict,
    }
