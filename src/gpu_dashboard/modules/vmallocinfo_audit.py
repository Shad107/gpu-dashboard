"""Module vmallocinfo_audit — kernel virtual allocation audit
(R&D #67.3).

/proc/vmallocinfo exposes every contiguous range allocated in
the kernel's vmalloc/ioremap virtual address space, along with
the function that allocated it. This is distinct from physical
memory accounting (slabinfo, buddyinfo, zoneinfo) — it tracks
*kernel virtual address space* pressure, which is what runs
out first when a driver leaks ioremap mappings.

Why it matters on a homelab desktop :

* NVIDIA driver upgrades (open vs proprietary) sometimes leak
  ioremap regions across module unload/reload — eventually the
  kernel refuses to map new GPU BARs.
* eBPF / DPDK / kdump workloads consume large vmalloc chunks.
  Knowing the single largest allocation pinpoints which.
* On 64-bit hosts the vmalloc space is huge (≥32 TiB) but the
  page-table overhead of a million tiny allocations is real.

The file is mode 0400 (kernel addresses are KASLR-sensitive).
An unprivileged daemon gets EACCES — we report this as
`requires_root` rather than crashing.

Verdicts (priority order) :
  vmalloc_giant_alloc      ≥1 single allocation ≥ 16 MiB
                          (often a runaway driver).
  vmalloc_fragmentation    ≥10 000 distinct allocations OR the
                          smallest 80 % of allocations sum to
                          ≤5 % of total bytes (huge skew).
  requires_root            file present but EACCES — running
                          unprivileged.
  ok                       file present, healthy distribution.
  unknown                  /proc/vmallocinfo absent (kernel
                          built without CONFIG_VMALLOC_INFO).

stdlib only.
"""
from __future__ import annotations

import os
import re
from typing import Dict, List, Optional, Tuple


NAME = "vmallocinfo_audit"


_PROC_VMALLOCINFO = "/proc/vmallocinfo"

_GIANT_ALLOC_BYTES = 16 * 1024 * 1024     # 16 MiB
_FRAG_COUNT = 10_000
_FRAG_TAIL_FRACTION = 0.80
_FRAG_TAIL_SUM_PCT = 0.05

_KIND_RE = re.compile(
    r"\b(ioremap|vmalloc|vmap|user|module|kasan)\b")


def parse_line(line: str) -> Optional[dict]:
    """Parse one /proc/vmallocinfo line.

    Format :
      <addr_start>-<addr_end>  <size>  <caller+off>  ... <kind>
    """
    line = line.strip()
    if not line:
        return None
    parts = line.split()
    if len(parts) < 3:
        return None
    try:
        size = int(parts[1])
    except ValueError:
        return None
    caller = parts[2]
    kinds = _KIND_RE.findall(line)
    kind = kinds[-1] if kinds else "unknown"
    return {"size": size, "caller": caller.split("+")[0],
              "kind": kind}


def parse_vmallocinfo(text: str) -> List[dict]:
    out: List[dict] = []
    for ln in text.splitlines():
        entry = parse_line(ln)
        if entry is not None:
            out.append(entry)
    return out


def aggregate(entries: List[dict]) -> dict:
    if not entries:
        return {"total_bytes": 0, "count": 0,
                "by_kind": {}, "top_callers": [],
                "largest": None}
    sizes = sorted(e["size"] for e in entries)
    by_kind: Dict[str, int] = {}
    by_caller: Dict[str, int] = {}
    largest = entries[0]
    for e in entries:
        by_kind[e["kind"]] = by_kind.get(e["kind"], 0) + e["size"]
        by_caller[e["caller"]] = (by_caller.get(e["caller"], 0)
                                       + e["size"])
        if e["size"] > largest["size"]:
            largest = e
    top_callers = sorted(by_caller.items(),
                              key=lambda kv: kv[1],
                              reverse=True)[:5]
    return {"total_bytes": sum(sizes),
              "count": len(entries),
              "by_kind": by_kind,
              "top_callers": [{"caller": c, "bytes": b}
                                for c, b in top_callers],
              "largest": largest,
              "sizes_sorted": sizes}


def _tail_fraction_sum(sizes_sorted: List[int],
                          fraction: float) -> int:
    if not sizes_sorted:
        return 0
    cutoff = int(len(sizes_sorted) * fraction)
    return sum(sizes_sorted[:cutoff])


def classify(file_present: bool, eacces: bool,
              agg: dict) -> dict:
    if not file_present:
        return {"verdict": "unknown",
                "reason": ("/proc/vmallocinfo absent — kernel "
                          "built without CONFIG_VMALLOC_INFO."),
                "recommendation": _recipe_unknown()}

    if eacces:
        return {"verdict": "requires_root",
                "reason": ("/proc/vmallocinfo is 0400 root-only ; "
                          "running as an unprivileged user."),
                "recommendation": _recipe_requires_root()}

    # 1) vmalloc_giant_alloc
    largest = agg.get("largest")
    if largest and largest["size"] >= _GIANT_ALLOC_BYTES:
        return {"verdict": "vmalloc_giant_alloc",
                "reason": (f"Largest single allocation : "
                          f"{largest['size'] // 1024} KiB by "
                          f"{largest['caller']} "
                          f"({largest['kind']})."),
                "recommendation": _recipe_giant_alloc(
                    largest["caller"])}

    # 2) vmalloc_fragmentation
    n = agg.get("count", 0)
    sizes_sorted = agg.get("sizes_sorted", [])
    if n >= _FRAG_COUNT:
        return {"verdict": "vmalloc_fragmentation",
                "reason": (f"{n:,} distinct allocations in "
                          f"vmalloc space — unusually high "
                          f"page-table overhead."),
                "recommendation": _recipe_fragmentation()}
    if n >= 100:
        total = agg.get("total_bytes", 0)
        tail_sum = _tail_fraction_sum(
            sizes_sorted, _FRAG_TAIL_FRACTION)
        if total > 0 and (tail_sum / total) <= _FRAG_TAIL_SUM_PCT:
            return {"verdict": "vmalloc_fragmentation",
                    "reason": (f"Bottom 80% of allocations sum to "
                              f"only {tail_sum/total:.1%} of total "
                              f"vmalloc bytes ({total:,}) — "
                              f"large-vs-tiny skew."),
                    "recommendation": _recipe_fragmentation()}

    return {"verdict": "ok",
            "reason": (f"{n} allocations, "
                      f"{agg.get('total_bytes', 0):,} bytes ; "
                      f"largest {largest['size']:,} bytes "
                      f"({largest['caller']})."
                          if largest
                          else f"{n} allocations."),
            "recommendation": ""}


def status(config=None,
            proc_path: str = _PROC_VMALLOCINFO) -> dict:
    file_present = os.path.exists(proc_path)
    eacces = False
    text = ""
    if file_present:
        try:
            with open(proc_path) as f:
                text = f.read()
        except PermissionError:
            eacces = True
        except OSError:
            eacces = True

    entries = parse_vmallocinfo(text) if text else []
    agg = aggregate(entries)
    verdict = classify(file_present, eacces, agg)

    return {"ok": file_present,
              "file_present": file_present,
              "permission_denied": eacces,
              "alloc_count": agg.get("count", 0),
              "total_bytes": agg.get("total_bytes", 0),
              "by_kind": agg.get("by_kind", {}),
              "top_callers": agg.get("top_callers", []),
              "largest": agg.get("largest"),
              "verdict": verdict}


# ── recovery recipes ────────────────────────────────────────────

def _recipe_unknown() -> str:
    return ("# /proc/vmallocinfo missing means CONFIG_VMALLOC_INFO\n"
            "# was disabled at build time. Rebuild the kernel or\n"
            "# install a distro debug kernel.\n")


def _recipe_requires_root() -> str:
    return ("# /proc/vmallocinfo is 0400 — run as root :\n"
            "sudo cat /proc/vmallocinfo | wc -l\n"
            "# Top 10 callers by bytes :\n"
            "sudo awk '{c=$3; gsub(/\\+.*/, \"\", c); a[c]+=$2} \n"
            "         END{for (k in a) print a[k], k}' \\\n"
            "    /proc/vmallocinfo | sort -nr | head\n")


def _recipe_giant_alloc(caller: str) -> str:
    return (f"# A single >16 MiB allocation by {caller} :\n"
            f"sudo grep '{caller}' /proc/vmallocinfo | sort -k2 -nr | head\n"
            f"# Identify module owning the caller :\n"
            f"sudo cat /proc/kallsyms | grep '{caller}'\n")


def _recipe_fragmentation() -> str:
    return ("# Many small vmalloc allocations. Identify worst\n"
            "# offenders :\n"
            "sudo awk '{c=$3; gsub(/\\+.*/, \"\", c); a[c]++}\n"
            "         END{for (k in a) print a[k], k}' \\\n"
            "    /proc/vmallocinfo | sort -nr | head\n"
            "# Unload the offending module if it's a leaky driver.\n")
