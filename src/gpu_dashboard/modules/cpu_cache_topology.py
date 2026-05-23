"""Module cpu_cache_topology — L3 cache topology placement advisor (R&D #37.4).

Modern CPUs split L3 across "islands" — Zen4 has one L3 per CCD
(2 CCDs on a 16-core 7950X = 2 islands of 32 MiB each), Alder/
Raptor Lake split P-core L3 from E-core L3. When threads from a
single inference daemon hop between L3 islands the KV-cache stops
fitting in L3 → trip to DDR per token → measurable TTFT increase.

This module reads /sys/devices/system/cpu/cpu*/cache/index*/{
level, size, shared_cpu_list, type}, finds distinct L3 islands
(by their shared_cpu_list value), and emits:

  single_l3          one L3 island covering all CPUs — no
                     preference matters
  multi_l3_islands   two or more L3 islands (Zen4 CCD, hybrid
                     P/E) → recipe pins to the largest island
                     via systemd CPUAffinity= or taskset
  no_l3              no L3 cache reported (very old or exotic)
  unknown            cache subsystem unreadable

stdlib only.
"""
from __future__ import annotations

import os
import re
from typing import Optional


NAME = "cpu_cache_topology"


_CPU_ROOT = "/sys/devices/system/cpu"
_CPU_ONLINE = "/sys/devices/system/cpu/online"


_RANGE_RE = re.compile(r"^(\d+)(?:-(\d+))?$")


def parse_cpu_list(s: Optional[str]) -> list:
    if not s:
        return []
    out: list = []
    for tok in s.strip().split(","):
        m = _RANGE_RE.match(tok.strip())
        if not m:
            continue
        a = int(m.group(1))
        b = int(m.group(2)) if m.group(2) else a
        out.extend(range(a, b + 1))
    return sorted(set(out))


_SIZE_RE = re.compile(r"^(\d+)\s*([KMG]?)\s*$", re.IGNORECASE)


def parse_size_bytes(s: Optional[str]) -> Optional[int]:
    if not s:
        return None
    m = _SIZE_RE.match(s.strip())
    if not m:
        return None
    n = int(m.group(1))
    unit = m.group(2).upper()
    mult = {"": 1, "K": 1024, "M": 1024 * 1024, "G": 1024 ** 3}.get(unit, 1)
    return n * mult


def _read(p: str) -> Optional[str]:
    try:
        with open(p) as f:
            return f.read().strip()
    except OSError:
        return None


_INDEX_RE = re.compile(r"^index(\d+)$")


def read_cache_indices(root: str, cpu_id: int) -> list:
    base = os.path.join(root, f"cpu{cpu_id}", "cache")
    try:
        names = sorted(os.listdir(base))
    except OSError:
        return []
    out: list = []
    for n in names:
        m = _INDEX_RE.match(n)
        if not m:
            continue
        d = os.path.join(base, n)
        lvl_s = _read(os.path.join(d, "level"))
        try:
            level = int(lvl_s) if lvl_s else 0
        except ValueError:
            level = 0
        out.append({
            "index": int(m.group(1)),
            "level": level,
            "size": _read(os.path.join(d, "size")) or "",
            "size_bytes": parse_size_bytes(_read(os.path.join(d, "size"))),
            "shared_cpu_list": _read(os.path.join(d, "shared_cpu_list")) or "",
            "type": _read(os.path.join(d, "type")) or "",
        })
    return out


def extract_l3_islands(root: str = _CPU_ROOT) -> list:
    """Walk every cpu*/cache/index* with level=3 and collect distinct
    shared_cpu_list values. Each unique list = one L3 island."""
    seen: dict = {}
    try:
        names = os.listdir(root)
    except OSError:
        return []
    for n in names:
        cm = re.match(r"^cpu(\d+)$", n)
        if not cm:
            continue
        cpu_id = int(cm.group(1))
        for idx in read_cache_indices(root, cpu_id):
            if idx["level"] != 3:
                continue
            key = idx["shared_cpu_list"]
            if not key or key in seen:
                continue
            seen[key] = {
                "cpu_list": key,
                "cpus": parse_cpu_list(key),
                "size_bytes": idx["size_bytes"],
            }
    return list(seen.values())


def _read_total_cpus(cpu_online: str) -> int:
    s = _read(cpu_online)
    return len(parse_cpu_list(s)) if s else 0


def _to_range_str(cpus: list) -> str:
    if not cpus:
        return ""
    out: list = []
    s = sorted(cpus)
    start = prev = s[0]
    for n in s[1:]:
        if n == prev + 1:
            prev = n
            continue
        out.append(str(start) if start == prev else f"{start}-{prev}")
        start = prev = n
    out.append(str(start) if start == prev else f"{start}-{prev}")
    return ",".join(out)


def classify(islands: list, total_cpus: int) -> dict:
    if not islands:
        return {"verdict": "no_l3",
                "reason": ("No L3 cache reported in /sys/devices/system/"
                           "cpu/cpu*/cache/index*. Very old / exotic CPU "
                           "or kernel without sysfs cache export."),
                "recommendation": ""}
    if len(islands) == 1:
        return {"verdict": "single_l3",
                "reason": (f"Single L3 island covering all {total_cpus} "
                           f"CPUs ({islands[0]['cpu_list']}, "
                           f"{(islands[0]['size_bytes'] or 0) // (1024 * 1024)} "
                           f"MiB) — no placement preference."),
                "recommendation": ""}
    # multi-island
    # Pick the largest by CPU count as the "primary" target
    largest = max(islands, key=lambda i: len(i["cpus"]))
    isl_summary = ", ".join(
        f"{i['cpu_list']} ({(i['size_bytes'] or 0) // (1024 * 1024)} MiB)"
        for i in islands)
    return {
        "verdict": "multi_l3_islands",
        "reason": (f"{len(islands)} L3 islands: {isl_summary}. KV-cache "
                   f"hopping between islands trips to DDR per token. "
                   f"Pin llama-server to a single island for locality."),
        "recommendation": (
            f"# Pin to the largest L3 island via systemd Drop-In:\n"
            f"sudo mkdir -p /etc/systemd/system/llama-server.service.d\n"
            f"sudo tee /etc/systemd/system/llama-server.service.d/cache_locality.conf <<'EOF'\n"
            f"[Service]\n"
            f"CPUAffinity={largest['cpu_list']}\n"
            f"EOF\n"
            f"sudo systemctl daemon-reload && sudo systemctl restart llama-server\n"
            f"# One-shot:\n"
            f"taskset -c {largest['cpu_list']} llama-server --model ...\n"
            f"# Companion: #37.2 gpu_cpu_affinity, #35.3 numa_placement."
        ),
    }


def _max_size_for_level(islands: list, root: str, level: int) -> Optional[int]:
    """Read every cpu0 index, find first matching level, return size in KiB."""
    for idx in read_cache_indices(root, 0):
        if idx["level"] == level and idx["size_bytes"]:
            return idx["size_bytes"] // 1024
    return None


def status(cfg=None) -> dict:
    total_cpus = _read_total_cpus(_CPU_ONLINE)
    islands = extract_l3_islands(_CPU_ROOT)
    verdict = classify(islands, total_cpus)
    summary_islands = [
        {**i, "size_mb": (i["size_bytes"] or 0) // (1024 * 1024)}
        for i in islands
    ]
    return {
        "ok": True,
        "total_cpus": total_cpus,
        "l3_island_count": len(islands),
        "islands": summary_islands,
        "l1d_kb": _max_size_for_level(islands, _CPU_ROOT, 1),
        "l2_kb": _max_size_for_level(islands, _CPU_ROOT, 2),
        "verdict": verdict,
    }
