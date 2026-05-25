"""Module zram_writeback_recompress_audit — zram writeback +
recompress posture (R&D #103.3).

Kernel 6.3+ adds an idle-page eviction + secondary-compression
pipeline to zram. On a desktop running heavy RAM swap, cold
pages should drift from zram to a `backing_dev` (cheap SSD),
and warm-but-cold pages should be re-compressed with a denser
algorithm (zstd, deflate) via `recomp_algorithm`.

The existing zswap_zram_audit (R&D #34.x era) only checks
zram exists + has a disksize + comp_algorithm. It does NOT
look at backing_dev / writeback / recomp_algorithm / bd_stat.

Reads :

  /sys/block/zram*/backing_dev
  /sys/block/zram*/recomp_algorithm
  /sys/block/zram*/disksize
  /sys/block/zram*/mm_stat
  /sys/block/zram*/bd_stat                 (writeback counters)

mm_stat columns (from kernel docs) :
  orig_data_size compr_data_size mem_used_total
  mem_limit mem_used_max same_pages pages_compacted

bd_stat columns :
  bd_count_reads bd_count_writes bd_count_total

Verdicts (worst-first) :

  backing_dev_pipeline_broken  err     backing_dev configured
                                       AND mm_stat shows heavy
                                       use, but bd_stat reports
                                       zero writes — pipeline
                                       wired but not flowing.
  zram_full_no_backing         warn    mm_stat compressed
                                       size > 50 % of disksize
                                       AND backing_dev=none —
                                       cold pages stuck in RAM.
  recomp_unset                 accent  no recomp_algorithm on
                                       kernel >= 6.2 — easy
                                       win missed.
  ok                                   pipeline healthy or
                                       zram unused.
  requires_root                        bd_stat / mm_stat
                                       unreadable.
  unknown                              no zram devices.

stdlib only.
"""
from __future__ import annotations

import os
from typing import Optional

NAME = "zram_writeback_recompress_audit"

DEFAULT_BLOCK = "/sys/block"


def _read_text(path: str) -> Optional[str]:
    try:
        with open(path, "r", encoding="utf-8") as fh:
            return fh.read()
    except (OSError, PermissionError):
        return None


def _read_str(path: str) -> Optional[str]:
    t = _read_text(path)
    return t.strip() if t is not None else None


def _read_int(path: str) -> Optional[int]:
    t = _read_text(path)
    if t is None:
        return None
    try:
        return int(t.strip())
    except ValueError:
        return None


def parse_mm_stat(text: Optional[str]) -> dict:
    """Return {orig_size, compr_size, mem_used_total, ...}."""
    out: dict = {}
    if not text:
        return out
    parts = text.split()
    keys = ("orig_size", "compr_size", "mem_used_total",
            "mem_limit", "mem_used_max", "same_pages",
            "pages_compacted")
    for i, k in enumerate(keys):
        if i >= len(parts):
            break
        try:
            out[k] = int(parts[i])
        except ValueError:
            continue
    return out


def parse_bd_stat(text: Optional[str]) -> dict:
    """Return {reads, writes, total} (units: 4 KiB pages)."""
    out: dict = {}
    if not text:
        return out
    parts = text.split()
    if len(parts) >= 3:
        try:
            out["reads"] = int(parts[0])
            out["writes"] = int(parts[1])
            out["total"] = int(parts[2])
        except ValueError:
            pass
    return out


def walk_zram(block_root: str = DEFAULT_BLOCK) -> list:
    """Return list of {name, disksize, backing_dev,
    recomp_algorithm, mm_stat, bd_stat}."""
    out: list = []
    if not os.path.isdir(block_root):
        return out
    try:
        entries = sorted(os.listdir(block_root))
    except OSError:
        return out
    for ent in entries:
        if not ent.startswith("zram"):
            continue
        d = os.path.join(block_root, ent)
        if not os.path.isdir(d):
            continue
        out.append({
            "name": ent,
            "disksize": _read_int(
                os.path.join(d, "disksize")),
            "backing_dev": _read_str(
                os.path.join(d, "backing_dev")),
            "recomp_algorithm": _read_str(
                os.path.join(d, "recomp_algorithm")),
            "mm_stat": parse_mm_stat(_read_text(
                os.path.join(d, "mm_stat"))),
            "bd_stat": parse_bd_stat(_read_text(
                os.path.join(d, "bd_stat"))),
        })
    return out


def classify(zrams: list, readable: bool) -> dict:
    if not zrams:
        return {"verdict": "unknown",
                "reason": (
                    "No zram devices — kernel without "
                    "CONFIG_ZRAM or modprobe never ran.")}
    if not readable:
        return {"verdict": "requires_root",
                "reason": (
                    "zram stats unreadable — re-run as "
                    "root.")}

    # err — backing_dev wired but no writes
    for z in zrams:
        bd = z.get("backing_dev") or ""
        mm = z.get("mm_stat") or {}
        bd_stat = z.get("bd_stat") or {}
        if (bd and bd != "none"
                and mm.get("compr_size", 0) > 0
                and bd_stat.get("writes", 0) == 0
                and bd_stat.get("total", 0) > 0):
            return {
                "verdict": "backing_dev_pipeline_broken",
                "reason": (
                    f"{z['name']}: backing_dev={bd} "
                    f"and zram is in use (compr_size="
                    f"{mm['compr_size']}) but bd_stat "
                    f"writes=0 — writeback pipeline wired "
                    "but no pages flowing out.")}

    # warn — zram heavily used + no backing
    for z in zrams:
        bd = z.get("backing_dev") or ""
        mm = z.get("mm_stat") or {}
        ds = z.get("disksize") or 0
        compr = mm.get("compr_size", 0)
        if (ds > 0 and compr > ds * 0.5
                and (not bd or bd == "none")):
            return {
                "verdict": "zram_full_no_backing",
                "reason": (
                    f"{z['name']}: compr_size={compr} "
                    f"is > 50% of disksize={ds} AND no "
                    "backing_dev. Cold pages stuck in "
                    "RAM ; configure a backing device.")}

    # accent — recomp_algorithm unset
    for z in zrams:
        ra = z.get("recomp_algorithm")
        if ra is None or ra == "":
            return {
                "verdict": "recomp_unset",
                "reason": (
                    f"{z['name']}: recomp_algorithm "
                    "unset. Kernel >= 6.2 supports "
                    "secondary compression — free density "
                    "win missed.")}

    return {"verdict": "ok",
            "reason": (
                f"{len(zrams)} zram device(s) ; "
                "backing/recomp pipeline coherent.")}


def status(config: Optional[dict] = None,
           block_root: str = DEFAULT_BLOCK) -> dict:
    zrams = walk_zram(block_root)
    # If devices exist but no fields could be read, that's
    # requires_root.
    readable = True
    if zrams:
        any_field_readable = any(
            z["mm_stat"] or z["bd_stat"]
            or z["disksize"] is not None
            for z in zrams)
        readable = any_field_readable
    verdict = classify(zrams, readable)
    return {
        "ok": verdict["verdict"] == "ok",
        "zram_count": len(zrams),
        "zrams": [
            {"name": z["name"],
             "disksize": z["disksize"],
             "backing_dev": z["backing_dev"],
             "recomp_algorithm": z["recomp_algorithm"],
             "compr_size": (
                 z["mm_stat"].get("compr_size")
                 if z["mm_stat"] else None),
             "bd_writes": (
                 z["bd_stat"].get("writes")
                 if z["bd_stat"] else None)}
            for z in zrams],
        "verdict": verdict,
    }
