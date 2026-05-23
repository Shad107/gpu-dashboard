"""Module memory_hotplug_audit — /sys/devices/system/memory (R&D #62.3).

Reads /sys/devices/system/memory/{block_size_bytes,
memory*/{state, valid_zones, removable}} + /proc/meminfo MemTotal
for cross-reference.

Why this matters on an LLM rig :

* A memory block stuck in `offline` after a failed balloon /
  CXL hot-remove silently shrinks usable RAM — `free` shows the
  smaller number, no kernel error remains in dmesg after the
  boot scrollback rolled, llama-server fails to mmap a 30 GB
  GGUF with a confusing ENOMEM.
* CXL / hotplug-capable hosts that put blocks into the Movable
  zone but never use them waste page-cache headroom.
* A non-removable block landing in Movable is a kernel bug
  marker (firmware mislabeled the SRAT).

Reads :
  /sys/devices/system/memory/block_size_bytes
  /sys/devices/system/memory/memory*/{state, valid_zones,
                                          removable}
  /proc/meminfo                                MemTotal cross-check

Verdicts (priority-ordered) :
  offline_blocks_present       ≥1 memory block in 'offline' state.
  non_removable_in_movable     ≥1 block with valid_zones=Movable
                               AND removable=0 (firmware SRAT
                               mislabel).
  movable_only_zone_skew       > 50 % of blocks are Movable-only —
                               kernel will refuse non-movable
                               allocations on that fraction.
  ok                           all blocks online + healthy.
  unsupported                  /sys/devices/system/memory absent
                               (kernel without
                               CONFIG_MEMORY_HOTPLUG).
  unknown                      sysfs subdir present but no
                               memory* blocks.

stdlib only.
"""
from __future__ import annotations

import os
import re
from typing import List, Optional


NAME = "memory_hotplug_audit"


_SYS_MEMORY = "/sys/devices/system/memory"
_PROC_MEMINFO = "/proc/meminfo"

_MEMORY_DIR_RE = re.compile(r"^memory\d+$")


def _read(p: str) -> Optional[str]:
    try:
        with open(p) as f:
            return f.read().strip()
    except OSError:
        return None


def _read_int(p: str) -> Optional[int]:
    t = _read(p)
    if t is None:
        return None
    try:
        return int(t, 0)
    except ValueError:
        return None


def list_memory_blocks(sys_memory: str = _SYS_MEMORY
                         ) -> List[dict]:
    if not os.path.isdir(sys_memory):
        return []
    out: List[dict] = []
    for name in sorted(os.listdir(sys_memory)):
        if not _MEMORY_DIR_RE.match(name):
            continue
        d = os.path.join(sys_memory, name)
        out.append({
            "id": name,
            "state": _read(os.path.join(d, "state")),
            "valid_zones": _read(os.path.join(d, "valid_zones")),
            "removable": _read_int(
                os.path.join(d, "removable")),
        })
    return out


def read_meminfo_total_kib(proc_meminfo: str = _PROC_MEMINFO
                              ) -> Optional[int]:
    text = _read(proc_meminfo)
    if not text:
        return None
    for line in text.splitlines():
        if line.startswith("MemTotal:"):
            parts = line.split()
            if len(parts) >= 2 and parts[1].isdigit():
                return int(parts[1])
    return None


def _is_movable_only(zones: Optional[str]) -> bool:
    """valid_zones is space-separated. 'Movable' alone (no Normal)
    means the block is Movable-only."""
    if not zones:
        return False
    toks = zones.split()
    return "Movable" in toks and "Normal" not in toks


def classify(blocks: List[dict],
              block_size: Optional[int],
              mem_total_kib: Optional[int],
              sys_memory_present: bool) -> dict:
    if not sys_memory_present:
        return {"verdict": "unsupported",
                "reason": ("/sys/devices/system/memory is absent — "
                          "kernel built without CONFIG_MEMORY_"
                          "HOTPLUG."),
                "recommendation": ""}

    if not blocks:
        return {"verdict": "unknown",
                "reason": ("memory sysfs subdir present but no "
                          "memory* blocks enumerated."),
                "recommendation": ""}

    # 1) offline_blocks_present
    offline = [b for b in blocks if b.get("state") == "offline"]
    if offline:
        sample = ", ".join(b["id"] for b in offline[:3])
        return {"verdict": "offline_blocks_present",
                "reason": (f"{len(offline)} memory block(s) "
                          f"offline : {sample}. Usable RAM "
                          f"silently shrunk."),
                "recommendation": _recipe_online()}

    # 2) non_removable_in_movable
    bad_label = [b for b in blocks
                    if _is_movable_only(b.get("valid_zones")) and
                       b.get("removable") == 0]
    if bad_label:
        sample = ", ".join(b["id"] for b in bad_label[:3])
        return {"verdict": "non_removable_in_movable",
                "reason": (f"{len(bad_label)} block(s) labelled "
                          f"Movable-only AND non-removable : "
                          f"{sample}. SRAT firmware mislabel."),
                "recommendation": _recipe_movable_bug()}

    # 3) movable_only_zone_skew — > 50 %
    movable_only = [b for b in blocks
                       if _is_movable_only(b.get("valid_zones"))]
    if len(blocks) > 0 and \
            len(movable_only) > len(blocks) * 0.5:
        pct = 100 * len(movable_only) / len(blocks)
        return {"verdict": "movable_only_zone_skew",
                "reason": (f"{pct:.0f}% of memory blocks "
                          f"({len(movable_only)}/{len(blocks)}) "
                          f"are Movable-only. Non-movable allocs "
                          f"may fail in low-memory."),
                "recommendation": _recipe_movable_skew()}

    return {"verdict": "ok",
            "reason": (f"{len(blocks)} memory block(s) online, "
                      f"block_size={block_size or '?'} bytes, "
                      f"MemTotal={mem_total_kib or '?'} KiB."),
            "recommendation": ""}


def status(config=None,
            sys_memory: str = _SYS_MEMORY,
            proc_meminfo: str = _PROC_MEMINFO) -> dict:
    sys_memory_present = os.path.isdir(sys_memory)
    block_size = _read_int(os.path.join(sys_memory,
                                              "block_size_bytes"))
    blocks = list_memory_blocks(sys_memory)
    mem_total = read_meminfo_total_kib(proc_meminfo)
    ok = sys_memory_present
    verdict = classify(blocks, block_size, mem_total,
                          sys_memory_present)
    return {"ok": ok,
              "sys_memory_present": sys_memory_present,
              "block_size_bytes": block_size,
              "block_count": len(blocks),
              "blocks_sample": blocks[:6],
              "mem_total_kib": mem_total,
              "verdict": verdict}


# ── recovery recipes ────────────────────────────────────────────

def _recipe_online() -> str:
    return ("# Bring offline blocks back online :\n"
            "for m in /sys/devices/system/memory/memory*; do\n"
            "  if [ \"$(cat $m/state)\" = offline ]; then\n"
            "    echo online | sudo tee $m/state\n"
            "  fi\n"
            "done\n"
            "# Then verify : grep MemTotal /proc/meminfo\n")


def _recipe_movable_bug() -> str:
    return ("# Movable-only + non-removable is usually a firmware\n"
            "# SRAT bug. Workaround : add 'movable_node' to GRUB\n"
            "# cmdline only if you actually want hot-removable\n"
            "# memory ; otherwise force kernel to treat as Normal :\n"
            "#   add 'memhp_default_state=online' to cmdline\n"
            "# Vendor BIOS update is the proper fix.\n")


def _recipe_movable_skew() -> str:
    return ("# Too much memory in Movable-only zone — the kernel\n"
            "# refuses non-movable allocations there. Boot with\n"
            "# 'memhp_default_state=online' (no movable suffix)\n"
            "# so new blocks land in Normal by default.\n")
