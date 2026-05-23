"""Module buddyinfo_frag — memory fragmentation auditor (R&D #34.2).

`/proc/buddyinfo` exposes the kernel's per-zone, per-order free-page
counts. Each line lists Node, zone name, and 11 counts (orders 0
through 10, where order N = 2^N × 4 KiB blocks). Order 9 = 2 MiB =
one Transparent Hugepage. Order 10 = 4 MiB.

When order 9 + 10 counts collapse toward zero, the host is
fragmented: the next THP allocation (or `alloc_pages(__GFP_NORETRY,
order=4+)` from a kernel driver) will trigger synchronous compaction,
which can stall inference threads 10-100 ms.

The classic LLM-rig failure chain:

  vm.swappiness=60 (#32.4) → anon swap-out
  → file pages reclaimed instead
  → fragmentation accumulates
  → next THP alloc compacts → TTFT stalls
  → caught here as `fragmented_severe`

Verdicts (Normal + DMA32 zones — DMA is tiny so ignored):
  ok                  >= 100 high-order (≥ order 9) pages total
  fragmented_moderate 10-99 high-order pages
  fragmented_severe   < 10 high-order pages — THP allocs likely
                      to trigger compaction
  unknown             buddyinfo absent

Recipe surfaces both immediate workarounds (`sysctl vm.compact_memory=1`,
`vm.drop_caches=3`) and structural pointers back to the cause-chain
modules (#32.4 swappiness, #34.1 THP defrag mode).

stdlib only.
"""
from __future__ import annotations

import os
import re
from typing import Optional


NAME = "buddyinfo_frag"


_BUDDYINFO = "/proc/buddyinfo"


_LINE_RE = re.compile(
    r"^Node\s+(\d+),\s+zone\s+(\S+)\s+((?:\d+\s*)+)\s*$"
)


def parse_buddyinfo(text: str) -> list:
    out: list = []
    if not text:
        return out
    for line in text.splitlines():
        m = _LINE_RE.match(line.strip())
        if not m:
            continue
        counts = [int(x) for x in m.group(3).split()]
        order9 = counts[9] if len(counts) > 9 else 0
        order10 = counts[10] if len(counts) > 10 else 0
        out.append({
            "node": int(m.group(1)),
            "zone": m.group(2),
            "counts": counts,
            "order9": order9,
            "order10": order10,
        })
    return out


def order_bytes(order: int, page_size: int = 4096) -> int:
    return (1 << order) * page_size


def total_free_bytes(counts: list, page_size: int = 4096) -> int:
    return sum(n * order_bytes(o, page_size) for o, n in enumerate(counts))


# Zones that matter for LLM allocations
_RELEVANT_ZONES = ("Normal", "DMA32")


_FRAG_SEVERE_THRESHOLD = 10      # < 10 high-order pages → severe
_FRAG_MODERATE_THRESHOLD = 100   # < 100 → moderate, else ok


_RANK = {
    "ok": 0,
    "unknown": 1,
    "fragmented_moderate": 2,
    "fragmented_severe": 3,
}


_RECIPE_FRAGMENTED = (
    "# Immediate (root) workarounds:\n"
    "echo 1 | sudo tee /proc/sys/vm/compact_memory   # force compact\n"
    "echo 3 | sudo tee /proc/sys/vm/drop_caches      # drop pagecache/dentries\n"
    "# Then re-check /proc/buddyinfo for order-9 recovery.\n"
    "# Root cause chain (in priority):\n"
    "# 1. #32.4 vm_sysctl_audit  vm.swappiness ≤ 10 → fewer anon evictions\n"
    "# 2. #34.1 thp_audit        defrag=defer+madvise → async compaction\n"
    "# 3. Add more host RAM if the chain is already clean."
)


def _high_order(z: dict) -> int:
    """Order 9 + order 10 page count, derived from `counts` when the
    pre-extracted keys aren't present (e.g. tests pass partial dicts)."""
    if "order9" in z and "order10" in z:
        return z["order9"] + z["order10"]
    counts = z.get("counts", [])
    return (counts[9] if len(counts) > 9 else 0) + (
        counts[10] if len(counts) > 10 else 0)


def classify(zones: list) -> dict:
    relevant = [z for z in zones if z["zone"] in _RELEVANT_ZONES]
    if not relevant:
        return {"verdict": "unknown",
                "reason": "No Normal/DMA32 zone in /proc/buddyinfo.",
                "recommendation": ""}
    worst = "ok"
    worst_zone = None
    for z in relevant:
        high_order = _high_order(z)
        if high_order < _FRAG_SEVERE_THRESHOLD:
            v = "fragmented_severe"
        elif high_order < _FRAG_MODERATE_THRESHOLD:
            v = "fragmented_moderate"
        else:
            v = "ok"
        if _RANK.get(v, 0) > _RANK.get(worst, 0):
            worst = v
            worst_zone = z
    if worst == "ok":
        return {"verdict": "ok",
                "reason": "All relevant zones have ample free hugepage blocks.",
                "recommendation": ""}
    high_order = _high_order(worst_zone)
    return {
        "verdict": worst,
        "reason": (f"{worst_zone['zone']} zone has only {high_order} free "
                   f"high-order (≥2 MiB) blocks. Next THP allocation "
                   f"will trigger sync compaction → 10-100 ms TTFT stalls."),
        "recommendation": _RECIPE_FRAGMENTED,
    }


def status(cfg=None) -> dict:
    if not os.path.exists(_BUDDYINFO):
        return {"ok": False, "error": "buddyinfo_unavailable",
                "reason": f"{_BUDDYINFO} not present."}
    try:
        with open(_BUDDYINFO) as f:
            text = f.read()
    except OSError:
        return {"ok": False, "error": "buddyinfo_unavailable",
                "reason": "Could not read buddyinfo."}
    zones = parse_buddyinfo(text)
    enriched: list = []
    total_thp_blocks = 0
    for z in zones:
        free_b = total_free_bytes(z["counts"])
        enriched.append({
            "node": z["node"],
            "zone": z["zone"],
            "counts": z["counts"],
            "order9_pages": z["order9"],
            "order10_pages": z["order10"],
            "total_free_mb": free_b // (1024 * 1024),
        })
        total_thp_blocks += z["order9"] + z["order10"]
    verdict = classify(zones)
    return {
        "ok": True,
        "zones": enriched,
        "total_thp_blocks": total_thp_blocks,
        "verdict": verdict,
        "worst_verdict": verdict["verdict"],
    }
