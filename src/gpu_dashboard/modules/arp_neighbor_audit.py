"""Module arp_neighbor_audit — IPv4 ARP / neighbor-cache
health (R&D #80.1).

Reads /proc/net/arp for per-neighbor state, /proc/net/stat/
arp_cache for kernel ARP GC counters, and the neighbor GC
thresholds in /proc/sys/net/ipv4/neigh/default/gc_thresh{1,2,3}.

Why this matters on a homelab box :

  When the NAS / router / PXE host flaps, the kernel records
  INCOMPLETE ARP entries in /proc/net/arp.  A user-visible
  "my NFS mount stalled for 30s" or "VS Code SSH keeps
  reconnecting" is often the only symptom.  The arp_cache
  GC counters and gc_thresh3 ceiling explain why : silent
  ARP-table evictions kicking in below an unsuspecting limit.

Verdicts (worst first) :

  arp_table_overflow       table_fulls > 0  OR  entries
                           ≥ gc_thresh3  (table actually
                           overflowed at least once).
  incomplete_neighbors_high  ≥ 5 INCOMPLETE neighbors —
                           multiple unreachable hosts on
                           the LAN.
  arp_table_high_watermark   entries ≥ 80 % of gc_thresh2
                           (forced GC about to kick in).
  ok                       table healthy, no incompletes.
  unknown                  /proc/net/arp unreadable.
"""
from __future__ import annotations

import os
from typing import Optional

DEFAULT_ARP = "/proc/net/arp"
DEFAULT_ARP_STAT = "/proc/net/stat/arp_cache"
DEFAULT_NEIGH_ROOT = "/proc/sys/net/ipv4/neigh/default"

# ARP flag bits — kernel/include/uapi/linux/if_arp.h
ATF_COM = 0x02


def _read_text(path: str) -> Optional[str]:
    try:
        with open(path, "r", encoding="utf-8") as fh:
            return fh.read()
    except (OSError, PermissionError):
        return None


def _read_int(path: str) -> Optional[int]:
    s = _read_text(path)
    if s is None:
        return None
    try:
        return int(s.strip())
    except ValueError:
        return None


def parse_arp(text: str) -> list[dict]:
    """Returns list of {ip, hw, flags, iface, complete}."""
    rows: list[dict] = []
    for i, line in enumerate(text.splitlines()):
        if i == 0:  # header
            continue
        cols = line.split()
        if len(cols) < 6:
            continue
        try:
            flags = int(cols[2], 16)
        except ValueError:
            continue
        rows.append({
            "ip": cols[0],
            "hw_type": cols[1],
            "flags": flags,
            "hw": cols[3],
            "iface": cols[5],
            "complete": bool(flags & ATF_COM),
        })
    return rows


def parse_arp_stat(text: str) -> Optional[dict]:
    """Aggregates per-CPU arp_cache stats into totals."""
    lines = text.splitlines()
    if len(lines) < 2:
        return None
    # Header columns
    header = lines[0].split()
    totals = {name: 0 for name in header}
    for line in lines[1:]:
        cols = line.split()
        if len(cols) != len(header):
            continue
        for name, val in zip(header, cols):
            try:
                totals[name] += int(val, 16)
            except ValueError:
                continue
    return totals


def read_neigh_thresholds(
        root: str = DEFAULT_NEIGH_ROOT) -> dict:
    return {
        "gc_thresh1": _read_int(os.path.join(root, "gc_thresh1")),
        "gc_thresh2": _read_int(os.path.join(root, "gc_thresh2")),
        "gc_thresh3": _read_int(os.path.join(root, "gc_thresh3")),
    }


def classify(arp: Optional[list[dict]],
             stats: Optional[dict],
             thresh: dict) -> dict:
    if arp is None:
        return {"verdict": "unknown",
                "reason": "/proc/net/arp unreadable."}

    incompletes = [r for r in arp if not r["complete"]]
    entries = len(arp)
    gc3 = thresh.get("gc_thresh3")
    gc2 = thresh.get("gc_thresh2")
    table_fulls = (stats or {}).get("table_fulls", 0)

    # 1. err — table overflow
    if table_fulls > 0:
        return {"verdict": "arp_table_overflow",
                "reason": (
                    f"arp_cache table_fulls = {table_fulls} "
                    "— ARP table overflowed (entries evicted "
                    "under pressure).")}
    if gc3 is not None and entries >= gc3:
        return {"verdict": "arp_table_overflow",
                "reason": (
                    f"{entries} entries ≥ gc_thresh3 {gc3} — "
                    "hard ARP-table ceiling reached.")}

    # 2. warn — many incomplete neighbors
    if len(incompletes) >= 5:
        return {"verdict": "incomplete_neighbors_high",
                "reason": (
                    f"{len(incompletes)} INCOMPLETE neighbor "
                    "entries — multiple unreachable LAN "
                    "hosts."),
                "incomplete_count": len(incompletes)}

    # 3. accent — approaching forced-GC watermark
    if gc2 is not None and gc2 > 0 and entries >= 0.8 * gc2:
        return {"verdict": "arp_table_high_watermark",
                "reason": (
                    f"{entries} entries ≥ 80 % of gc_thresh2 "
                    f"({gc2}) — forced GC imminent."),
                "entries": entries,
                "gc_thresh2": gc2}

    # 4. ok
    return {"verdict": "ok",
            "reason": (
                f"{entries} ARP entries ; "
                f"{len(incompletes)} incomplete ; "
                f"table_fulls=0.")}


def status(config: Optional[dict] = None,
           arp_path: str = DEFAULT_ARP,
           stat_path: str = DEFAULT_ARP_STAT,
           neigh_root: str = DEFAULT_NEIGH_ROOT) -> dict:
    arp_text = _read_text(arp_path)
    arp = parse_arp(arp_text) if arp_text is not None else None
    stat_text = _read_text(stat_path)
    stats = parse_arp_stat(stat_text) if stat_text else None
    thresh = read_neigh_thresholds(neigh_root)
    verdict = classify(arp, stats, thresh)
    return {
        "ok": verdict["verdict"] not in (
            "unknown", "arp_table_overflow"),
        "entries": len(arp) if arp else 0,
        "incomplete_count": sum(
            1 for r in (arp or []) if not r["complete"]),
        "table_fulls": (stats or {}).get("table_fulls", 0),
        "gc_thresholds": thresh,
        "verdict": verdict,
    }
