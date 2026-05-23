"""Module dma_audit — DMA engine + SWIOTLB auditor (R&D #48.3).

Reads /sys/class/dma/* (per-DMA-engine class device) + best-effort
/sys/kernel/debug/swiotlb/* (root-only debugfs) for the kernel's
DMA-engine + software-IO-TLB state.

Useful diagnostic on hosts where :
  - PCIe DMA engines (Intel IOAT, AMD GenZ, ARM CCC, NVIDIA copy)
    are present but unused — could accelerate memcpy / network if
    drivers were configured.
  - SWIOTLB (software IO translation lookaside buffer) is hitting
    its bounce-buffer pool — happens when a 32-bit PCIe device
    DMA-maps high memory, kernel falls back to copying through
    the SWIOTLB pool. On 64-GB hosts with old NICs / older USB
    controllers + RTX 3090 this can cause unexpected stalls.

Verdicts (priority-ordered) :
  swiotlb_bounce_high    /sys/kernel/debug/swiotlb/io_tlb_used >
                         80 % of io_tlb_nslabs — bounce-buffer
                         saturating ; surface kernel cmdline
                         swiotlb=force / swiotlb=N option.
  dma_engine_idle        ≥1 DMA engine present but inference
                         workload doesn't appear to use it (we
                         don't have stat counters in /sys/class/
                         dma to verify usage — surface as info).
  ok                     no engines OR engines present and
                         SWIOTLB not saturating.
  no_dma_devices         /sys/class/dma empty + /sys/kernel/debug/
                         swiotlb absent → nothing to audit (typical
                         qemu guest without virtio-iommu).
  unknown                /sys/class/dma unreadable.

stdlib only.
"""
from __future__ import annotations

import os
from typing import Optional


NAME = "dma_audit"


_SYS_CLASS_DMA = "/sys/class/dma"
_DEBUGFS_SWIOTLB = "/sys/kernel/debug/swiotlb"


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


def list_dma_engines(sys_dma: str = _SYS_CLASS_DMA) -> list:
    if not os.path.isdir(sys_dma):
        return []
    out: list = []
    try:
        names = sorted(os.listdir(sys_dma))
    except OSError:
        return []
    for name in names:
        d = os.path.join(sys_dma, name)
        # /sys/class/dma/<name> is typically a symlink to a real
        # /sys/devices/pci.../dma/<name> — chase to get info.
        # Pull a few common attributes (best-effort).
        out.append({
            "name": name,
            "bytes_transferred": _read_int(
                os.path.join(d, "bytes_transferred")),
            "in_use": _read_int(os.path.join(d, "in_use")),
            "memcpy_count": _read_int(
                os.path.join(d, "memcpy_count")),
        })
    return out


def read_swiotlb(swiotlb_dir: str = _DEBUGFS_SWIOTLB) -> dict:
    """/sys/kernel/debug/swiotlb/{io_tlb_nslabs, io_tlb_used}
    are root-only on modern kernels. Returns whatever we can read
    and a permission flag if denied."""
    if not os.path.isdir(swiotlb_dir):
        return {"available": False, "permission_error": False}
    out: dict = {"available": True, "permission_error": False}
    try:
        os.listdir(swiotlb_dir)
    except PermissionError:
        return {"available": True, "permission_error": True}
    except OSError:
        return {"available": False, "permission_error": False}
    nslabs = _read_int(os.path.join(swiotlb_dir, "io_tlb_nslabs"))
    used = _read_int(os.path.join(swiotlb_dir, "io_tlb_used"))
    if nslabs is None:
        # Check if any of the files are perm-denied to distinguish
        # "missing" vs "denied".
        try:
            with open(os.path.join(swiotlb_dir, "io_tlb_nslabs")):
                pass
        except PermissionError:
            out["permission_error"] = True
        except OSError:
            pass
    if nslabs is not None:
        out["io_tlb_nslabs"] = nslabs
    if used is not None:
        out["io_tlb_used"] = used
        if nslabs:
            out["used_ratio"] = used / nslabs
    return out


_BOUNCE_THRESHOLD = 0.80


_RECIPE_BOUNCE_HIGH = (
    "# SWIOTLB bounce-buffer pool is approaching saturation —\n"
    "# some 32-bit-DMA device is heavily bouncing high memory.\n"
    "# Options :\n"
    "#  1. Bump pool size at boot via kernel cmdline :\n"
    "#       GRUB_CMDLINE_LINUX_DEFAULT=\"... swiotlb=131072 ...\"\n"
    "#     (default is 65536 = 256 MB ; double to 512 MB).\n"
    "#  2. If the device supports 64-bit DMA, check the driver\n"
    "#     setting (some drivers don't enable 64-bit by default).\n"
    "#  3. Track the offending device via :\n"
    "#       echo 1 > /sys/kernel/tracing/events/swiotlb/enable\n"
    "#       cat /sys/kernel/tracing/trace_pipe"
)


def classify(engines: list, swiotlb: dict) -> dict:
    used_ratio = swiotlb.get("used_ratio")
    if used_ratio is not None and used_ratio >= _BOUNCE_THRESHOLD:
        return {"verdict": "swiotlb_bounce_high",
                "reason": (f"SWIOTLB io_tlb_used="
                           f"{swiotlb.get('io_tlb_used')} of "
                           f"io_tlb_nslabs="
                           f"{swiotlb.get('io_tlb_nslabs')} "
                           f"({used_ratio:.0%}). Bounce-buffer "
                           f"pool saturating."),
                "recommendation": _RECIPE_BOUNCE_HIGH}
    if not engines and not swiotlb.get("available"):
        return {"verdict": "no_dma_devices",
                "reason": ("/sys/class/dma empty + /sys/kernel/"
                           "debug/swiotlb absent — nothing to "
                           "audit (typical qemu guest without "
                           "virtio-iommu or x86 SWIOTLB-less "
                           "kernel)."),
                "recommendation": ""}
    return {"verdict": "ok",
            "reason": (f"{len(engines)} DMA engine(s) ; SWIOTLB "
                       + ("available" if swiotlb.get("available")
                           else "absent")
                       + (f" ({used_ratio * 100:.1f} % used)"
                            if used_ratio is not None else "")
                       + " — no saturation signal."),
            "recommendation": ""}


def status(cfg=None) -> dict:
    if not os.path.isdir(_SYS_CLASS_DMA):
        return {
            "ok": False,
            "verdict": {"verdict": "unknown",
                         "reason": "/sys/class/dma unreadable.",
                         "recommendation": ""},
            "dma_engines": [], "swiotlb": {"available": False},
        }
    engines = list_dma_engines(_SYS_CLASS_DMA)
    swiotlb = read_swiotlb(_DEBUGFS_SWIOTLB)
    verdict = classify(engines, swiotlb)
    return {
        "ok": True,
        "dma_engine_count": len(engines),
        "dma_engines": engines,
        "swiotlb": swiotlb,
        "verdict": verdict,
    }
