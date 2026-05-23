"""Module pagetypeinfo_audit — buddy allocator fragmentation (R&D #57.2).

Parses /proc/pagetypeinfo to surface buddy-allocator fragmentation
*by migrate-type and order*. Distinct from any existing buddyinfo
audit (which is order-only, not migrate-type aware) — pagetypeinfo
catches the LLM-host foot-gun where Unmovable allocations
contaminate the Movable migrate-type, starving order-9 (2 MiB)
allocations that THP / KVM / DPDK need.

Why this matters :

* llama.cpp `mmap()` of a 30 GB GGUF benefits from THP. If the
  Movable pool is fragmented (no contiguous order-9 blocks left),
  THP falls back to 4 KiB pages → prefill throughput craters.
* Unmovable allocations in Movable blocks ("type pollution") is
  the root cause and is invisible to vmstat / buddyinfo.

Reads :
  /proc/pagetypeinfo                  (root-only, 0400 typically)
  /proc/sys/vm/extfrag_threshold

Verdicts (priority-ordered) :
  unmovable_in_movable        ≥1 Movable block contains Unmovable
                              pages (kernel-tracked type
                              pollution).
  high_order_starved          0 free pages at order ≥ 7 in any
                              Movable zone.
  moderate_frag               extfrag_threshold > 500 AND
                              order-6 free count low.
  ok                          counters healthy.
  requires_root               /proc/pagetypeinfo not readable as
                              the daemon user.
  unknown                     /proc/pagetypeinfo absent.

stdlib only.
"""
from __future__ import annotations

import os
import re
from typing import Dict, List, Optional


NAME = "pagetypeinfo_audit"


_PROC_PAGETYPEINFO = "/proc/pagetypeinfo"
_PROC_EXTFRAG = "/proc/sys/vm/extfrag_threshold"


_FREE_LINE_RE = re.compile(
    r"^Node\s+(?P<node>\d+),\s+zone\s+(?P<zone>\S+),\s+"
    r"type\s+(?P<type>\S+)\s+(?P<orders>.+)$")

_BLOCK_HEADER_RE = re.compile(
    r"^Number of blocks type\s+(?P<types>.+)$")

_BLOCK_LINE_RE = re.compile(
    r"^Node\s+(?P<node>\d+),\s+zone\s+(?P<zone>\S+)\s+"
    r"(?P<counts>.+)$")


def _read(p: str) -> Optional[str]:
    try:
        with open(p) as f:
            return f.read()
    except PermissionError:
        return "__EACCES__"
    except OSError:
        return None


def _read_int(p: str) -> Optional[int]:
    t = _read(p)
    if t is None or t == "__EACCES__":
        return None
    try:
        return int(t.strip())
    except ValueError:
        return None


def parse_pagetypeinfo(text: Optional[str]) -> dict:
    """Parse /proc/pagetypeinfo into structured form.

    Returns {free_pages: [{node,zone,type,orders:[int...]}, …],
             block_counts: [{node,zone,types:{typename: count, …}}, …]}.
    """
    out: dict = {"free_pages": [], "block_counts": [],
                   "block_type_header": []}
    if not text or text == "__EACCES__":
        return out
    in_blocks_section = False
    type_header: List[str] = []
    for raw in text.splitlines():
        line = raw.rstrip()
        if not line:
            continue
        m = _BLOCK_HEADER_RE.match(line)
        if m:
            type_header = m.group("types").split()
            out["block_type_header"] = type_header
            in_blocks_section = True
            continue
        if in_blocks_section:
            m = _BLOCK_LINE_RE.match(line)
            if m:
                counts = m.group("counts").split()
                # Trim leading 'type' label if present
                if counts and counts[0].endswith(","):
                    counts = counts[1:]
                # The block-count line has N integer columns where
                # N == len(type_header). Truncate to that.
                if type_header:
                    counts = counts[-len(type_header):]
                try:
                    cmap = {
                        type_header[i]: int(counts[i])
                        for i in range(min(len(type_header),
                                              len(counts)))
                    }
                except ValueError:
                    continue
                out["block_counts"].append({
                    "node": int(m.group("node")),
                    "zone": m.group("zone"),
                    "types": cmap,
                })
            continue
        m = _FREE_LINE_RE.match(line)
        if m:
            try:
                orders = [int(x) for x in
                            m.group("orders").split()]
            except ValueError:
                continue
            out["free_pages"].append({
                "node": int(m.group("node")),
                "zone": m.group("zone"),
                "type": m.group("type"),
                "orders": orders,
            })
    return out


def classify(parsed: dict, perm_denied: bool,
              extfrag_threshold: Optional[int]) -> dict:
    if perm_denied:
        return {"verdict": "requires_root",
                "reason": ("/proc/pagetypeinfo is mode 0400 — only "
                          "root can read it. Run the dashboard as "
                          "root for the full view."),
                "recommendation": _recipe_root_view()}

    free = parsed.get("free_pages") or []
    blocks = parsed.get("block_counts") or []

    if not free and not blocks:
        return {"verdict": "unknown",
                "reason": "/proc/pagetypeinfo unparseable.",
                "recommendation": ""}

    # 1) unmovable_in_movable — kernel-tracked type pollution.
    # We look at "Number of blocks" rows : if Movable blocks
    # exist with Unmovable migrate-type counts mixed in. The
    # canonical signal is /proc/pagetypeinfo's 'Number of blocks'
    # row showing Unmovable > 0 in the Movable zone count line.
    # Heuristic : block_counts entry where Unmovable >
    # 5 % of Movable.
    polluted = []
    for entry in blocks:
        types = entry.get("types") or {}
        unmovable = types.get("Unmovable", 0)
        movable = types.get("Movable", 0)
        if movable > 0 and unmovable > 0 and \
                unmovable > movable * 0.05:
            polluted.append(
                f"node{entry['node']}/{entry['zone']} "
                f"U={unmovable}/M={movable}")
    if polluted:
        return {"verdict": "unmovable_in_movable",
                "reason": (f"{len(polluted)} zone(s) show "
                          f"Unmovable pollution > 5 % of Movable "
                          f"blocks : {polluted[0]}."),
                "recommendation": _recipe_compact()}

    # 2) high_order_starved — no free pages at order ≥ 7 in any
    #    Movable zone.
    starved = []
    for f in free:
        if f.get("type") != "Movable":
            continue
        orders = f.get("orders") or []
        if len(orders) > 7 and sum(orders[7:]) == 0:
            starved.append(
                f"node{f['node']}/{f['zone']}")
    if starved:
        return {"verdict": "high_order_starved",
                "reason": (f"{len(starved)} Movable zone(s) with "
                          f"no free pages at order ≥ 7 : "
                          f"{starved[0]}. THP allocs fall back "
                          f"to 4 KiB."),
                "recommendation": _recipe_compact()}

    # 3) moderate_frag — sysctl threshold relaxed and order-6
    #    counts thin.
    if extfrag_threshold is not None and extfrag_threshold > 500:
        thin = False
        for f in free:
            if f.get("type") == "Movable":
                orders = f.get("orders") or []
                if len(orders) > 6 and orders[6] < 4:
                    thin = True
                    break
        if thin:
            return {"verdict": "moderate_frag",
                    "reason": (f"extfrag_threshold = "
                              f"{extfrag_threshold} (> 500) and "
                              f"order-6 Movable counts are thin."),
                    "recommendation": _recipe_compact()}

    return {"verdict": "ok",
            "reason": (f"{len(free)} free-page rows, "
                      f"{len(blocks)} block rows ; allocator "
                      f"fragmentation healthy."),
            "recommendation": ""}


def status(config=None,
            proc_pagetypeinfo: str = _PROC_PAGETYPEINFO,
            proc_extfrag: str = _PROC_EXTFRAG) -> dict:
    raw = _read(proc_pagetypeinfo)
    perm_denied = raw == "__EACCES__"
    parsed = parse_pagetypeinfo(
        raw if raw not in (None, "__EACCES__") else "")
    extfrag = _read_int(proc_extfrag)
    ok = (raw is not None and raw != "__EACCES__") or perm_denied
    verdict = classify(parsed, perm_denied, extfrag)
    return {"ok": ok,
              "permission_denied": perm_denied,
              "free_page_rows": len(parsed.get("free_pages", [])),
              "block_rows": len(parsed.get("block_counts", [])),
              "extfrag_threshold": extfrag,
              "verdict": verdict}


# ── recovery recipes ────────────────────────────────────────────

def _recipe_root_view() -> str:
    return ("# Inspect pagetypeinfo as root :\n"
            "sudo cat /proc/pagetypeinfo | head -40\n"
            "# Look at the 'Free pages count per migrate type'\n"
            "# rows for Movable — zero counts at orders 7-10 mean\n"
            "# THP fallback to 4 KiB pages.\n")


def _recipe_compact() -> str:
    return ("# Trigger immediate memory compaction :\n"
            "echo 1 | sudo tee /proc/sys/vm/compact_memory\n"
            "# Adjust the sysctl extfrag_threshold to be more\n"
            "# aggressive about background compaction :\n"
            "echo 500 | sudo tee /proc/sys/vm/extfrag_threshold\n"
            "# Persist via /etc/sysctl.d/99-extfrag.conf.\n")
