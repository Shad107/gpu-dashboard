"""Module zfs_arc_audit — ZFS ARC tuning + RAM-pressure
detector (R&D #97.2).

No existing module touches /proc/spl or /sys/module/zfs.
`swap_tunables_audit`, `vm_tuning_deep`, and the cgroup-
memory audits all ignore ARC entirely. This audit owns
the ZFS Adaptive Replacement Cache (ARC) surface that on
homelabs running a model dir off a zpool can squeeze CUDA
pinned-memory allocations.

Reads :

  /proc/spl/kstat/zfs/arcstats           current ARC stats
                                          (size, c_min, c_max,
                                          meta_used,
                                          meta_limit)
  /sys/module/zfs/parameters/zfs_arc_max bytes (0 = auto)
  /sys/module/zfs/parameters/zfs_arc_min bytes
  /proc/meminfo                          MemTotal cross-ref

Verdicts (worst-first) :

  arc_unbounded         err    zfs_arc_max = 0 on a host
                               with > 32 GiB RAM — ARC can
                               grow to fill MemAvailable.
  arc_eating_ram        warn   current size > 50 % of
                               MemTotal — competing with
                               GPU pinned memory.
  arc_meta_pressure     accent meta_used > 90 % of
                               meta_limit — metadata churn.
  ok                    ARC sized sanely.
  requires_root         arcstats / params mode-400.
  unknown               ZFS not loaded.

stdlib only.
"""
from __future__ import annotations

import os
import re
from typing import Optional

NAME = "zfs_arc_audit"

DEFAULT_ARCSTATS = "/proc/spl/kstat/zfs/arcstats"
DEFAULT_ZFS_PARAMS = "/sys/module/zfs/parameters"
DEFAULT_PROC_MEMINFO = "/proc/meminfo"

# Threshold for "arc_unbounded on big-RAM box".
_BIG_RAM_BYTES = 32 * 2**30
# 50% of MemTotal threshold for arc_eating_ram.
_EATING_RAM_PCT = 0.50
# 90% of meta limit threshold for accent.
_META_PRESSURE_PCT = 0.90

_MEMTOTAL_RE = re.compile(r"^MemTotal:\s*(\d+)\s*kB",
                          re.MULTILINE)


def _read_text(path: str) -> Optional[str]:
    try:
        with open(path, "r", encoding="utf-8") as fh:
            return fh.read()
    except (OSError, PermissionError):
        return None


def _read_int(path: str) -> Optional[int]:
    t = _read_text(path)
    if t is None:
        return None
    try:
        return int(t.strip())
    except ValueError:
        return None


def parse_meminfo_total_bytes(text: str) -> Optional[int]:
    if not text:
        return None
    m = _MEMTOTAL_RE.search(text)
    if not m:
        return None
    try:
        return int(m.group(1)) * 1024
    except ValueError:
        return None


def parse_arcstats(text: str) -> dict:
    """Parse /proc/spl/kstat/zfs/arcstats lines like
    'name <type> <value>'. Returns dict of int values
    for keys of interest."""
    out: dict = {}
    if not text:
        return out
    wanted = (
        "size", "c_min", "c_max", "hits", "misses",
        "arc_meta_used", "arc_meta_limit",
    )
    for line in text.splitlines():
        parts = line.split()
        if len(parts) < 3:
            continue
        if parts[0] in wanted:
            try:
                out[parts[0]] = int(parts[2])
            except ValueError:
                continue
    return out


def read_params(root: str = DEFAULT_ZFS_PARAMS) -> dict:
    return {
        "zfs_arc_max": _read_int(
            os.path.join(root, "zfs_arc_max")),
        "zfs_arc_min": _read_int(
            os.path.join(root, "zfs_arc_min")),
    }


def classify(zfs_loaded: bool,
             arcstats_readable: bool,
             arcstats: dict,
             params: dict,
             mem_total: Optional[int]) -> dict:
    if not zfs_loaded:
        return {"verdict": "unknown",
                "reason": (
                    "ZFS module not loaded (no /proc/spl "
                    "or /sys/module/zfs).")}
    if not arcstats_readable:
        return {"verdict": "requires_root",
                "reason": (
                    "/proc/spl/kstat/zfs/arcstats unreadable "
                    "— re-run as root.")}

    # err — unbounded on big-RAM
    arc_max = params.get("zfs_arc_max")
    if (arc_max == 0 and mem_total is not None
            and mem_total > _BIG_RAM_BYTES):
        return {
            "verdict": "arc_unbounded",
            "reason": (
                "zfs_arc_max = 0 (auto) on a "
                f"{mem_total / 2**30:.0f} GiB host — ARC "
                "can grow to fill MemAvailable and squeeze "
                "CUDA pinned memory. Set a hard cap.")}

    # warn — current ARC eating RAM
    size = arcstats.get("size") or 0
    if (mem_total is not None
            and size > _EATING_RAM_PCT * mem_total):
        return {
            "verdict": "arc_eating_ram",
            "reason": (
                f"ARC size = {size / 2**30:.1f} GiB "
                f"({100 * size / mem_total:.0f}% of "
                f"{mem_total / 2**30:.0f} GiB) — competing "
                "with GPU pinned memory. Cap via "
                "zfs_arc_max.")}

    # accent — metadata pressure
    meta_used = arcstats.get("arc_meta_used") or 0
    meta_limit = arcstats.get("arc_meta_limit") or 0
    if (meta_limit > 0
            and meta_used > _META_PRESSURE_PCT * meta_limit):
        return {
            "verdict": "arc_meta_pressure",
            "reason": (
                f"ARC meta = {meta_used / 2**20:.0f} MiB "
                f"({100 * meta_used / meta_limit:.0f}% of "
                "meta_limit) — metadata churn high. Consider "
                "bumping zfs_arc_meta_limit.")}

    return {"verdict": "ok",
            "reason": (
                f"ARC size = {size / 2**30:.1f} GiB ; "
                f"zfs_arc_max = "
                f"{(arc_max or 0) / 2**30:.1f} GiB.")}


def status(config: Optional[dict] = None,
           arcstats_path: str = DEFAULT_ARCSTATS,
           zfs_params: str = DEFAULT_ZFS_PARAMS,
           meminfo_path: str = DEFAULT_PROC_MEMINFO) -> dict:
    zfs_loaded = (
        os.path.isfile(arcstats_path)
        or os.path.isdir(zfs_params))
    arcstats_text = _read_text(arcstats_path)
    arcstats_readable = arcstats_text is not None
    arcstats = parse_arcstats(arcstats_text or "")
    params = read_params(zfs_params)
    mem_total = parse_meminfo_total_bytes(
        _read_text(meminfo_path) or "")
    verdict = classify(zfs_loaded, arcstats_readable,
                       arcstats, params, mem_total)
    return {
        "ok": verdict["verdict"] == "ok",
        "zfs_loaded": zfs_loaded,
        "arc_size": arcstats.get("size"),
        "arc_c_max": arcstats.get("c_max"),
        "zfs_arc_max_param": params.get("zfs_arc_max"),
        "mem_total": mem_total,
        "verdict": verdict,
    }
