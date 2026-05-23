"""Module zswap_zram_audit — compressed-swap auditor (R&D #41.1).

When a 70B-quant (~40 GB) lands on a 32-GB-RAM host, the first
swap-pageout latency cliff hits inference t/s the moment context
grows past ~3K tokens. Linux offers two compressed-swap layers :

  zswap     pool of LRU-compressed pages in front of a real
            backing swap device. /sys/module/zswap/parameters/{
              enabled, compressor, zpool, max_pool_percent,
              accept_threshold_percent
            }
  zram      RAM-backed compressed block device — no disk backing.
            /sys/block/zram*/{disksize, comp_algorithm, mm_stat,
            io_stat, max_comp_streams}

Together they absorb 60-80 % of swap traffic with single-digit-ms
compression latency instead of NVMe's 200-2000 µs read latency.
The recurring foot-guns :
  (1) zswap.enabled=N (default on most distros) + 24 GB quant
      loaded → every page-out hits NVMe directly.
  (2) zswap.compressor=lzo (legacy default) + Zen4/Zen5 host →
      lz4 is 2-3× faster on tensor blobs.
  (3) zswap.max_pool_percent=20 (default) on a 32-GB box →
      pool capped at ~6.4 GB, fills instantly under pressure.
  (4) zswap.zpool=zbud (oldest, ~50 % ratio) vs z3fold (~67 %)
      vs zsmalloc (~3-4×, default since 6.6).

Verdicts (priority-ordered) :
  zswap_disabled_on_tight_box  swap active + zswap.enabled=N on
                               a ≤ 48-GB host with active swap.
  legacy_compressor            zswap on but compressor=lzo or zpool=
                               zbud — older defaults, lz4 + zsmalloc
                               are strictly better.
  pool_too_small               zswap on + max_pool_percent ≤ 20 on
                               a tight box.
  zram_idle_when_useful        zram device exists but swapon shows
                               only NVMe — zram never wired up.
  ok_configured                zswap enabled + modern compressor +
                               pool ≥ 30 %.
  ok_not_needed                big-RAM box, swap inactive — fine.
  unknown                      /sys/module/zswap not present.

stdlib only.
"""
from __future__ import annotations

import os
import re
from typing import Optional


NAME = "zswap_zram_audit"


_SYS_ZSWAP = "/sys/module/zswap/parameters"
_SYS_BLOCK = "/sys/block"
_PROC_SWAPS = "/proc/swaps"
_MEMINFO = "/proc/meminfo"


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


def _read_bool_yn(p: str) -> Optional[bool]:
    t = _read(p)
    if t is None:
        return None
    s = t.strip().upper()
    if s == "Y":
        return True
    if s == "N":
        return False
    return None


def read_zswap(sys_z: str = _SYS_ZSWAP) -> dict:
    if not os.path.isdir(sys_z):
        return {"available": False}
    state: dict = {"available": True}
    state["enabled"] = _read_bool_yn(os.path.join(sys_z, "enabled"))
    state["compressor"] = (_read(os.path.join(sys_z, "compressor"))
                              or "").strip() or None
    state["zpool"] = (_read(os.path.join(sys_z, "zpool"))
                       or "").strip() or None
    state["max_pool_percent"] = _read_int(
        os.path.join(sys_z, "max_pool_percent"))
    state["accept_threshold_percent"] = _read_int(
        os.path.join(sys_z, "accept_threshold_percent"))
    return state


def read_zram_devices(sys_block: str = _SYS_BLOCK) -> list:
    if not os.path.isdir(sys_block):
        return []
    out: list = []
    for name in sorted(os.listdir(sys_block)):
        if not name.startswith("zram"):
            continue
        ddir = os.path.join(sys_block, name)
        out.append({
            "name": name,
            "disksize": _read_int(os.path.join(ddir, "disksize")),
            "comp_algorithm": (_read(os.path.join(
                ddir, "comp_algorithm")) or "").strip() or None,
            "max_comp_streams": _read_int(
                os.path.join(ddir, "max_comp_streams")),
            "mm_stat_raw": (_read(os.path.join(ddir, "mm_stat"))
                              or "").strip() or None,
        })
    return out


def read_swap_devices(proc_swaps: str = _PROC_SWAPS) -> list:
    text = _read(proc_swaps) or ""
    lines = [l for l in text.splitlines() if l.strip()]
    out: list = []
    for line in lines[1:]:  # skip header
        parts = line.split()
        if len(parts) >= 5:
            out.append({"path": parts[0], "type": parts[1],
                          "size_kb": int(parts[2])
                            if parts[2].isdigit() else None,
                          "used_kb": int(parts[3])
                            if parts[3].isdigit() else None,
                          "priority": parts[4]})
    return out


def read_mem_total_gb(meminfo_path: str = _MEMINFO) -> Optional[float]:
    text = _read(meminfo_path) or ""
    for line in text.splitlines():
        if line.startswith("MemTotal:"):
            parts = line.split()
            try:
                return int(parts[1]) / (1024 * 1024)
            except (ValueError, IndexError):
                return None
    return None


_LEGACY_COMPRESSORS = ("lzo", "lzo-rle")
_LEGACY_ZPOOLS = ("zbud", "z3fold")


_RECIPE_ENABLE_ZSWAP = (
    "# Enable zswap with modern defaults on a tight LLM rig :\n"
    "# Runtime (effective until reboot) :\n"
    "echo 1 | sudo tee /sys/module/zswap/parameters/enabled\n"
    "echo lz4 | sudo tee /sys/module/zswap/parameters/compressor\n"
    "echo zsmalloc | sudo tee /sys/module/zswap/parameters/zpool\n"
    "echo 40 | sudo tee /sys/module/zswap/parameters/max_pool_percent\n"
    "# Persistent : /etc/default/grub GRUB_CMDLINE_LINUX_DEFAULT must\n"
    "# include : zswap.enabled=1 zswap.compressor=lz4\n"
    "#            zswap.zpool=zsmalloc zswap.max_pool_percent=40\n"
    "# Then sudo update-grub && reboot."
)

_RECIPE_MODERN_COMPRESSOR = (
    "# Switch zswap to lz4 + zsmalloc — strictly better than legacy\n"
    "# lzo + zbud / z3fold defaults on every modern CPU :\n"
    "echo lz4 | sudo tee /sys/module/zswap/parameters/compressor\n"
    "echo zsmalloc | sudo tee /sys/module/zswap/parameters/zpool\n"
    "# Persist via GRUB_CMDLINE_LINUX_DEFAULT."
)

_RECIPE_RAISE_POOL = (
    "# Pool too small for a memory-tight LLM rig — bump to 40 % so\n"
    "# zswap has room to absorb context-growth swap pressure :\n"
    "echo 40 | sudo tee /sys/module/zswap/parameters/max_pool_percent"
)

_RECIPE_ENABLE_ZRAM = (
    "# A zram block device exists but is not wired into /proc/swaps.\n"
    "# Activate it as a high-priority swap source (zram-based swap\n"
    "# is RAM-resident, no disk wear) :\n"
    "sudo mkswap /dev/zram0\n"
    "sudo swapon -p 100 /dev/zram0   # highest priority\n"
    "# Persist : install zram-tools or systemd-zram-generator package."
)


def _is_tight_box(mem_total_gb: Optional[float]) -> bool:
    return mem_total_gb is not None and mem_total_gb <= 48


def _swap_active(swap_devs: list) -> bool:
    return any(d.get("used_kb", 0) and d["used_kb"] > 0
               for d in swap_devs) or len(swap_devs) > 0


def _swap_is_only_disk(swap_devs: list) -> bool:
    return bool(swap_devs) and not any(
        "zram" in d.get("path", "") for d in swap_devs)


def classify(zswap: dict, zram_devs: list, swap_devs: list,
              mem_total_gb: Optional[float]) -> dict:
    if not zswap.get("available"):
        return {"verdict": "unknown",
                "reason": ("/sys/module/zswap not present — zswap "
                           "module not loaded by this kernel."),
                "recommendation": ""}
    enabled = zswap.get("enabled")
    swap_on = _swap_active(swap_devs)
    if not swap_on and not _is_tight_box(mem_total_gb):
        return {"verdict": "ok_not_needed",
                "reason": (f"Swap inactive on a "
                           f"{mem_total_gb:.0f}-GB host — compressed-"
                           f"swap layer is informational only."),
                "recommendation": ""}
    # 1) zswap off + tight box with active swap.
    if enabled is False and swap_on and _is_tight_box(mem_total_gb):
        return {"verdict": "zswap_disabled_on_tight_box",
                "reason": (f"{mem_total_gb:.0f}-GB host with active "
                           f"swap and zswap disabled — every swap-"
                           f"out hits the backing device directly. "
                           f"Enabling zswap absorbs 60-80 % of swap "
                           f"traffic with single-digit-ms latency."),
                "recommendation": _RECIPE_ENABLE_ZSWAP}
    # 2) zswap on but using legacy compressor / zpool.
    if enabled is True:
        comp = (zswap.get("compressor") or "").lower()
        zp = (zswap.get("zpool") or "").lower()
        if comp in _LEGACY_COMPRESSORS or zp in _LEGACY_ZPOOLS:
            return {"verdict": "legacy_compressor",
                    "reason": (f"zswap on but compressor={comp or '?'} "
                               f"/ zpool={zp or '?'} — lz4 + zsmalloc "
                               f"are strictly better on every modern "
                               f"CPU (faster + higher ratio)."),
                    "recommendation": _RECIPE_MODERN_COMPRESSOR}
        # 3) pool too small on tight box.
        pool_pct = zswap.get("max_pool_percent") or 0
        if pool_pct <= 20 and _is_tight_box(mem_total_gb):
            return {"verdict": "pool_too_small",
                    "reason": (f"zswap.max_pool_percent={pool_pct} on "
                               f"a {mem_total_gb:.0f}-GB host — the "
                               f"compressed pool fills instantly "
                               f"under context-growth pressure."),
                    "recommendation": _RECIPE_RAISE_POOL}
    # 4) zram device exists but unused.
    if zram_devs and _swap_is_only_disk(swap_devs):
        sized = [z for z in zram_devs if (z.get("disksize") or 0) > 0]
        if sized:
            return {"verdict": "zram_idle_when_useful",
                    "reason": (f"{len(sized)} zram block device(s) "
                               f"exist but no zram entry is wired "
                               f"into /proc/swaps — RAM-resident "
                               f"swap layer is unused."),
                    "recommendation": _RECIPE_ENABLE_ZRAM}
    return {"verdict": "ok_configured",
            "reason": ("zswap enabled + modern compressor / zpool ; "
                       "pool size appropriate for this host."),
            "recommendation": ""}


def status(cfg=None) -> dict:
    zswap = read_zswap(_SYS_ZSWAP)
    zram_devs = read_zram_devices(_SYS_BLOCK)
    swap_devs = read_swap_devices(_PROC_SWAPS)
    mem_total_gb = read_mem_total_gb(_MEMINFO)
    verdict = classify(zswap, zram_devs, swap_devs, mem_total_gb)
    return {
        "ok": zswap.get("available", False),
        "zswap": zswap,
        "zram_devices": zram_devs,
        "swap_devices": swap_devs,
        "mem_total_gb": mem_total_gb,
        "verdict": verdict,
    }
