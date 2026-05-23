"""Module swap_tunables_audit — swap-pathway runtime knobs (R&D #54.2).

Distinct from existing modules :
  * vm_sysctl_audit / vm_tuning_deep cover general vm.* sysctls.
  * zswap_zram_audit covers compression backends (enabled / params).
  * This module focuses on the *swap-pathway readahead + watermark
    + device-type* tunables that quietly destroy LLM throughput :
       - page-cluster (8 pages per swap-in by default) is murder
         on zram (compresses 8× the data per fault).
       - swappiness=60 is the kernel default but is too high for
         an LLM host with a 24 GB VRAM workload.
       - swap on rotational media never recovers from cache miss.
       - tiny min_free_kbytes makes kswapd thrash under VRAM-spill.

Reads :
  /proc/sys/vm/{swappiness, page-cluster, watermark_scale_factor,
                  watermark_boost_factor, min_free_kbytes,
                  extfrag_threshold}
  /sys/kernel/mm/swap/vma_ra_enabled
  /proc/swaps                       # active swap devices
  /sys/block/<dev>/queue/rotational
  /sys/block/zram*/disksize          # detect active zram
  /sys/bus/pci/devices/*/vendor      # NVIDIA detection
  /sys/bus/pci/devices/*/class

Verdicts (priority-ordered) :
  swap_on_hdd                      ≥1 active swap device is on a
                                   rotational block device.
  high_swappiness_with_gpu         NVIDIA GPU present and
                                   swappiness ≥ 30.
  tiny_min_free                    min_free_kbytes < 0.5 % of
                                   /proc/meminfo MemTotal.
  page_cluster_default_on_zram     active zram device AND
                                   page-cluster > 0.
  ok                               tunables match an LLM host.
  unknown                          /proc/sys/vm not readable.

stdlib only.
"""
from __future__ import annotations

import os
import re
from typing import List, Optional


NAME = "swap_tunables_audit"


_PROC_SYS_VM = "/proc/sys/vm"
_SYS_MM_SWAP = "/sys/kernel/mm/swap"
_PROC_SWAPS = "/proc/swaps"
_SYS_BLOCK = "/sys/block"
_PROC_MEMINFO = "/proc/meminfo"
_SYS_PCI = "/sys/bus/pci/devices"


_NVIDIA_VENDOR = "0x10de"
# PCI base class 0x03 = display controller
_DISPLAY_BASE_CLASS = 0x03


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
        return int(t)
    except ValueError:
        return None


def read_vm_knobs(proc_sys_vm: str = _PROC_SYS_VM) -> dict:
    if not os.path.isdir(proc_sys_vm):
        return {"available": False}
    out: dict = {"available": True}
    for k in ("swappiness", "page-cluster", "watermark_scale_factor",
                "watermark_boost_factor", "min_free_kbytes",
                "extfrag_threshold"):
        out[k] = _read_int(os.path.join(proc_sys_vm, k))
    return out


def read_swap_mm(sys_mm_swap: str = _SYS_MM_SWAP) -> dict:
    if not os.path.isdir(sys_mm_swap):
        return {"available": False}
    return {
        "available": True,
        "vma_ra_enabled": _read(os.path.join(sys_mm_swap,
                                                  "vma_ra_enabled"))
    }


def read_swaps(proc_swaps: str = _PROC_SWAPS,
                 sys_block: str = _SYS_BLOCK) -> List[dict]:
    text = _read(proc_swaps)
    if not text:
        return []
    lines = text.splitlines()
    # First line is header; skip it.
    out: List[dict] = []
    for line in lines[1:]:
        parts = line.split()
        if len(parts) < 4:
            continue
        path, kind, size, used = parts[:4]
        device = _swap_device_for_path(path, sys_block)
        rotational = None
        if device:
            rotational = _read_int(
                os.path.join(sys_block, device, "queue",
                                "rotational"))
        out.append({"path": path, "type": kind,
                      "size_kib": int(size) if size.isdigit() else None,
                      "used_kib": int(used) if used.isdigit() else None,
                      "device": device, "rotational": rotational})
    return out


def _swap_device_for_path(path: str, sys_block: str) -> Optional[str]:
    """Best-effort : map a /proc/swaps path to a /sys/block/<name>.
    Falls back to first SCSI / virtio / zram block dev that
    contains '/swap' in /etc/fstab — but we keep it simple : if
    path starts with /dev/<dev>, use <dev> ; else statvfs find
    the mountpoint device."""
    if path.startswith("/dev/"):
        dev = os.path.basename(path)
        # Strip trailing partition digit for sda2 → sda  / nvme0n1p2 → nvme0n1
        m = re.match(r"^(.*?)(?:p?\d+)?$", dev)
        return m.group(1) if m else dev
    # File-backed swap — try statvfs's fs_dev mapping. Simplest :
    # walk /sys/block and look at the *first* non-loop, non-zram
    # device. Imperfect, but better than nothing.
    if not os.path.isdir(sys_block):
        return None
    for name in sorted(os.listdir(sys_block)):
        if name.startswith(("loop", "zram", "ram")):
            continue
        return name
    return None


def detect_active_zram(sys_block: str = _SYS_BLOCK) -> List[str]:
    if not os.path.isdir(sys_block):
        return []
    out: List[str] = []
    for name in sorted(os.listdir(sys_block)):
        if not name.startswith("zram"):
            continue
        size = _read_int(os.path.join(sys_block, name, "disksize"))
        if size and size > 0:
            out.append(name)
    return out


def has_nvidia_gpu(sys_pci: str = _SYS_PCI) -> bool:
    if not os.path.isdir(sys_pci):
        return False
    for bdf in os.listdir(sys_pci):
        ddir = os.path.join(sys_pci, bdf)
        vendor = _read(os.path.join(ddir, "vendor"))
        klass = _read(os.path.join(ddir, "class"))
        if vendor != _NVIDIA_VENDOR or not klass:
            continue
        try:
            base = (int(klass, 16) >> 16) & 0xff
        except ValueError:
            continue
        if base == _DISPLAY_BASE_CLASS:
            return True
    return False


def read_mem_total_kib(proc_meminfo: str = _PROC_MEMINFO
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


def classify(vm: dict, swap_mm: dict, swaps: List[dict],
              zram_active: List[str], gpu_present: bool,
              mem_total_kib: Optional[int]) -> dict:
    if not vm.get("available"):
        return {"verdict": "unknown",
                "reason": "/proc/sys/vm not readable.",
                "recommendation": ""}

    # 1) swap_on_hdd
    hdd_swaps = [s for s in swaps if s.get("rotational") == 1]
    if hdd_swaps:
        sample = ", ".join(s["path"] for s in hdd_swaps[:2])
        return {"verdict": "swap_on_hdd",
                "reason": (f"{len(hdd_swaps)} swap device(s) on "
                          f"rotational media : {sample}."),
                "recommendation": _recipe_swap_off_hdd()}

    # 2) high_swappiness_with_gpu
    swappiness = vm.get("swappiness")
    if gpu_present and swappiness is not None and swappiness >= 30:
        return {"verdict": "high_swappiness_with_gpu",
                "reason": (f"NVIDIA GPU present and swappiness = "
                          f"{swappiness}. Host memory churn mid-"
                          f"inference becomes likely."),
                "recommendation": _recipe_lower_swappiness()}

    # 3) tiny_min_free
    min_free = vm.get("min_free_kbytes")
    if (mem_total_kib and min_free is not None and
            mem_total_kib > 0 and
            min_free < mem_total_kib * 0.005):
        pct = (min_free / mem_total_kib) * 100
        return {"verdict": "tiny_min_free",
                "reason": (f"min_free_kbytes = {min_free} KiB "
                          f"= {pct:.2f}% of MemTotal. kswapd will "
                          f"thrash under VRAM-spill."),
                "recommendation": _recipe_min_free()}

    # 4) page_cluster_default_on_zram
    pc = vm.get("page-cluster")
    if zram_active and pc is not None and pc > 0:
        return {"verdict": "page_cluster_default_on_zram",
                "reason": (f"page-cluster = {pc} (reads "
                          f"{1 << pc} pages per fault) on zram "
                          f"({', '.join(zram_active)}) — "
                          f"compresses {1 << pc}× the data."),
                "recommendation": _recipe_zram_page_cluster()}

    return {"verdict": "ok",
            "reason": (f"swap tunables look healthy "
                      f"(swappiness={swappiness}, page-cluster="
                      f"{pc}, min_free_kbytes={min_free})."),
            "recommendation": ""}


def status(config=None,
            proc_sys_vm: str = _PROC_SYS_VM,
            sys_mm_swap: str = _SYS_MM_SWAP,
            proc_swaps: str = _PROC_SWAPS,
            sys_block: str = _SYS_BLOCK,
            proc_meminfo: str = _PROC_MEMINFO,
            sys_pci: str = _SYS_PCI) -> dict:
    vm = read_vm_knobs(proc_sys_vm)
    swap_mm = read_swap_mm(sys_mm_swap)
    swaps = read_swaps(proc_swaps, sys_block)
    zram_active = detect_active_zram(sys_block)
    gpu_present = has_nvidia_gpu(sys_pci)
    mem_total_kib = read_mem_total_kib(proc_meminfo)
    ok = vm.get("available", False)
    verdict = classify(vm, swap_mm, swaps, zram_active,
                          gpu_present, mem_total_kib)
    return {"ok": ok,
              "vm_knobs": vm,
              "swap_mm": swap_mm,
              "swaps": swaps,
              "zram_active": zram_active,
              "gpu_present": gpu_present,
              "mem_total_kib": mem_total_kib,
              "verdict": verdict}


# ── recovery recipes ────────────────────────────────────────────

def _recipe_swap_off_hdd() -> str:
    return ("# Move swap to an SSD or to zram :\n"
            "sudo swapoff -a\n"
            "# Edit /etc/fstab to comment the HDD swap entry, then :\n"
            "sudo apt install zram-tools  # Debian/Ubuntu\n"
            "# … or create a swap file on an SSD partition.\n")


def _recipe_lower_swappiness() -> str:
    return ("# Lower swappiness so the kernel keeps inference\n"
            "# pages resident :\n"
            "echo 10 | sudo tee /proc/sys/vm/swappiness\n"
            "# Persist via /etc/sysctl.d/99-llm.conf :\n"
            "#   vm.swappiness = 10\n")


def _recipe_min_free() -> str:
    return ("# Raise min_free_kbytes so kswapd has headroom :\n"
            "echo 1048576 | sudo tee /proc/sys/vm/min_free_kbytes  # 1 GiB\n"
            "# Persist via /etc/sysctl.d/99-llm.conf :\n"
            "#   vm.min_free_kbytes = 1048576\n")


def _recipe_zram_page_cluster() -> str:
    return ("# zram compresses each fault — read 1 page per fault\n"
            "# instead of 8 :\n"
            "echo 0 | sudo tee /proc/sys/vm/page-cluster\n"
            "# Persist via /etc/sysctl.d/99-llm.conf :\n"
            "#   vm.page-cluster = 0\n")
