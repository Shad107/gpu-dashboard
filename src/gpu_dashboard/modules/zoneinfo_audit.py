"""Module zoneinfo_audit — /proc/zoneinfo + /proc/vmstat (R&D #43.3).

Shipped #40.3 vm_tuning_deep covers the *configurable* knobs
(page-cluster, watermark_scale_factor, vfs_cache_pressure, …).
This module covers the *runtime telemetry* :

  /proc/zoneinfo               per-zone {min, low, high, managed,
                                free} watermarks (page counts).
  /proc/vmstat                 system-wide reclaim/compaction +
                                allocstall counters.

The actionable signals are :

  pgsteal_direct          increasing = the kernel is doing
                          direct-reclaim ; inference workers
                          stall 50-200 ms per event.
  allocstall_*            increasing = direct-reclaim attempts.
  compact_fail            compaction tried + gave up — THP /
                          high-order allocations are slower.
  compact_stall           compaction took a synchronous stall.
  zone.free <= zone.low   the zone is at or below the low
                          watermark ; kswapd is woken.

Verdicts (priority-ordered) :
  direct_reclaim_active   pgsteal_direct > 0 and growing as a
                          fraction of pgsteal_kswapd — direct
                          reclaim is happening *now* (snapshot)
                          or has recently happened ; surface for
                          tuning attention.
  compaction_failures     compact_fail / (compact_success + 1) > 0.2
                          → compaction is failing more than it
                          succeeds ; THP / KV-cache hugepage
                          allocs will fall back to 4 KB pages.
  zone_low                ≥1 zone has nr_free_pages ≤ low — kswapd
                          is being woken right now.
  ok                      no direct reclaim, compaction healthy,
                          zones above low watermark.
  unknown                 /proc/zoneinfo unreadable.

stdlib only.
"""
from __future__ import annotations

import os
import re
from typing import Optional


NAME = "zoneinfo_audit"


_PROC_ZONEINFO = "/proc/zoneinfo"
_PROC_VMSTAT = "/proc/vmstat"


def _read(p: str) -> Optional[str]:
    try:
        with open(p) as f:
            return f.read()
    except OSError:
        return None


_NODE_RE = re.compile(r"^Node\s+(\d+),\s+zone\s+(\S+)")
_KV_RE = re.compile(r"^\s+(\S+)\s+(-?\d+)$")


def parse_zoneinfo(text: str) -> list:
    """Return [{node, zone, free, min, low, high, managed, ...}]."""
    out: list = []
    cur: Optional[dict] = None
    for line in text.splitlines():
        m = _NODE_RE.match(line)
        if m:
            if cur is not None:
                out.append(cur)
            cur = {"node": int(m.group(1)), "zone": m.group(2)}
            continue
        if cur is None:
            continue
        kv = _KV_RE.match(line)
        if kv:
            key = kv.group(1)
            try:
                val = int(kv.group(2))
            except ValueError:
                continue
            # We only care about the watermark + free + managed
            # fields ; ignore the per-cpu-pageset block, NUMA
            # stats, etc., to keep the payload small.
            if key in ("pages", "min", "low", "high", "managed",
                        "nr_free_pages"):
                # 'pages' line is "pages free N" not "pages N" —
                # handled below.
                cur[key] = val
            continue
        # Handle "pages free N" (no leading numeric value after key)
        m2 = re.match(r"^\s+pages free\s+(\d+)$", line)
        if m2 and cur is not None:
            try:
                cur["free"] = int(m2.group(1))
            except ValueError:
                pass
    if cur is not None:
        out.append(cur)
    # Drop the "per-node stats" pseudo-zone (those lines bear NUMA-
    # node aggregate counters, not zone watermarks).
    return [z for z in out if z.get("zone") != "stats"]


def parse_vmstat(text: str) -> dict:
    out: dict = {}
    for line in text.splitlines():
        parts = line.split()
        if len(parts) != 2:
            continue
        try:
            out[parts[0]] = int(parts[1])
        except ValueError:
            continue
    return out


_RECIPE_DIRECT_RECLAIM = (
    "# Direct reclaim is happening — inference workers will stall\n"
    "# 50-200 ms per event. Bump watermark_scale_factor so kswapd\n"
    "# wakes earlier (shipped #40.3 vm_tuning_deep recipe) :\n"
    "echo 200 | sudo tee /proc/sys/vm/watermark_scale_factor\n"
    "echo 'vm.watermark_scale_factor = 200' | \\\n"
    "  sudo tee /etc/sysctl.d/99-watermark.conf"
)

_RECIPE_COMPACTION = (
    "# Compaction is failing more than half the time. Two options :\n"
    "#  1. Disable THP defrag (avoids the sync-compaction stalls\n"
    "#     entirely — cross-ref shipped #31.1 thp_audit) :\n"
    "echo defer+madvise | \\\n"
    "  sudo tee /sys/kernel/mm/transparent_hugepage/defrag\n"
    "#  2. Or bump min_free_kbytes so compaction has buffer room :\n"
    "echo 262144 | sudo tee /proc/sys/vm/min_free_kbytes"
)

_RECIPE_ZONE_LOW = (
    "# At least one memory zone is at/below the low watermark —\n"
    "# kswapd is being woken right now. Triggers : a memory-heavy\n"
    "# workload (large quant load, big inference context), a\n"
    "# memory leak, or undersized RAM. Bump watermark_scale_factor\n"
    "# (early wake) + min_free_kbytes (more headroom) :\n"
    "echo 200 | sudo tee /proc/sys/vm/watermark_scale_factor\n"
    "echo 262144 | sudo tee /proc/sys/vm/min_free_kbytes"
)


def classify(zones: list, vmstat: dict) -> dict:
    if not zones and not vmstat:
        return {"verdict": "unknown",
                "reason": "/proc/zoneinfo unreadable.",
                "recommendation": ""}
    # 1) Direct reclaim active.
    pgsteal_direct = vmstat.get("pgsteal_direct", 0)
    pgsteal_kswapd = vmstat.get("pgsteal_kswapd", 0)
    if pgsteal_direct > 0 and pgsteal_kswapd > 0:
        direct_share = pgsteal_direct / (
            pgsteal_direct + pgsteal_kswapd)
        if direct_share >= 0.10:
            return {"verdict": "direct_reclaim_active",
                    "reason": (f"pgsteal_direct={pgsteal_direct} of "
                               f"total stolen pages "
                               f"({direct_share:.0%}). Inference "
                               f"workers will stall 50-200 ms each "
                               f"time direct reclaim fires."),
                    "recommendation": _RECIPE_DIRECT_RECLAIM}
    # 2) Compaction failures.
    cf = vmstat.get("compact_fail", 0)
    cs = vmstat.get("compact_success", 0)
    if cf > 0 and cf / (cs + 1) >= 0.20:
        return {"verdict": "compaction_failures",
                "reason": (f"compact_fail={cf} vs compact_success="
                           f"{cs} — compaction is failing "
                           f"{cf / (cs + cf) * 100:.0f} % of the "
                           f"time. THP / hugepage allocations "
                           f"fall back to 4 KB pages."),
                "recommendation": _RECIPE_COMPACTION}
    # 3) Any zone at/below low watermark.
    zone_low = [z for z in zones
                  if isinstance(z.get("free"), int)
                  and isinstance(z.get("low"), int)
                  and z["free"] <= z["low"] and z["low"] > 0]
    if zone_low:
        names = ", ".join(
            f"node {z['node']}/{z['zone']} "
            f"(free={z['free']}, low={z['low']})"
            for z in zone_low[:5])
        return {"verdict": "zone_low",
                "reason": (f"{len(zone_low)} zone(s) at or below "
                           f"the low watermark — kswapd is being "
                           f"woken. {names}"),
                "recommendation": _RECIPE_ZONE_LOW}
    return {"verdict": "ok",
            "reason": (f"{len(zones)} zone(s) above low watermark ; "
                       f"direct reclaim "
                       f"{pgsteal_direct / max(pgsteal_kswapd, 1) * 100:.1f} % "
                       f"of kswapd ; compaction fail rate "
                       f"{cf / max(cs + cf, 1) * 100:.0f} %."),
            "recommendation": ""}


def status(cfg=None) -> dict:
    text_z = _read(_PROC_ZONEINFO) or ""
    text_v = _read(_PROC_VMSTAT) or ""
    zones = parse_zoneinfo(text_z)
    vmstat = parse_vmstat(text_v)
    verdict = classify(zones, vmstat)
    # Cherry-pick the headline vmstat fields the UI cares about.
    head_vm = {k: vmstat[k] for k in (
        "pgsteal_kswapd", "pgsteal_direct",
        "pgscan_kswapd", "pgscan_direct",
        "pgscan_direct_throttle",
        "allocstall_normal", "allocstall_movable",
        "compact_success", "compact_fail", "compact_stall",
        "compact_daemon_wake", "nr_free_pages",
    ) if k in vmstat}
    return {
        "ok": bool(zones) or bool(vmstat),
        "zone_count": len(zones),
        "zones": zones,
        "vmstat": head_vm,
        "verdict": verdict,
    }
