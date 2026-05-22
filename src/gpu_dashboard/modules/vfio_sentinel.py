"""Module vfio_sentinel — detect GPUs bound to vfio-pci (R&D #9.1).

LLM-rig / homelab pain : you set up a GPU for VM passthrough, the host's
nvidia-smi can't see the card any more, and you don't know which VM has it.
This module walks /sys/bus/pci/devices/*/driver, identifies NVIDIA GPUs
(vendor ID 0x10de) bound to vfio-pci instead of nvidia, then cross-refs
/proc/<pid>/cmdline for qemu-system-* processes using `-device vfio-pci`.

stdlib only. Silent if no PCI GPUs are VFIO-bound (zero overhead).
"""
from __future__ import annotations

import glob
import os
import re
import time
from typing import Optional


NAME = "vfio_sentinel"

# Standard NVIDIA PCI vendor ID
_NVIDIA_VENDOR = "0x10de"


def _read_file(path: str) -> Optional[str]:
    try:
        with open(path) as f:
            return f.read().strip()
    except (OSError, UnicodeDecodeError):
        return None


def _resolve_driver(device_dir: str) -> Optional[str]:
    """Return the driver bound to this PCI device (or None if unbound)."""
    link = os.path.join(device_dir, "driver")
    if not os.path.islink(link):
        return None
    target = os.readlink(link)  # e.g. '../../../bus/pci/drivers/vfio-pci'
    return os.path.basename(target)


def list_nvidia_gpus_with_state() -> list:
    """Return list of {bdf, vendor_id, device_id, driver, is_vfio} for each
    NVIDIA-vendor PCI device."""
    out: list = []
    for dev in sorted(glob.glob("/sys/bus/pci/devices/*")):
        vendor = _read_file(os.path.join(dev, "vendor"))
        if vendor != _NVIDIA_VENDOR:
            continue
        bdf = os.path.basename(dev)
        device_id = _read_file(os.path.join(dev, "device")) or ""
        driver = _resolve_driver(dev)
        cls = _read_file(os.path.join(dev, "class")) or ""
        # 0x030000 = VGA controller, 0x030200 = 3D controller, 0x040300 = audio
        is_gpu = cls.startswith("0x030") or cls.startswith("0x03")
        if not is_gpu:
            continue
        out.append({
            "bdf": bdf,
            "vendor_id": vendor,
            "device_id": device_id,
            "driver": driver or "unbound",
            "is_vfio": driver == "vfio-pci",
            "class": cls,
        })
    return out


def find_qemu_holders_for_bdf(bdf: str) -> list:
    """Find qemu processes that have this PCI BDF in their cmdline.

    Looks for either `-device vfio-pci,host=<bdf>` or `host=<bdf_short>`
    (BDF without the leading 0000: domain prefix).
    """
    short_bdf = bdf.split(":", 1)[1] if bdf.startswith("0000:") else bdf
    holders: list = []
    for proc_dir in glob.glob("/proc/[0-9]*"):
        cmdline = _read_file(os.path.join(proc_dir, "cmdline"))
        if not cmdline:
            continue
        # cmdline uses null-byte separators
        clean = cmdline.replace("\0", " ")
        if "qemu" not in clean.lower():
            continue
        if bdf in clean or short_bdf in clean:
            try:
                pid = int(os.path.basename(proc_dir))
            except ValueError:
                continue
            # Extract a friendly name (e.g. -name flag, fallback to qemu binary)
            name = None
            m = re.search(r"-name\s+([^\s,]+)", clean)
            if m:
                name = m.group(1)
            else:
                m = re.search(r"(qemu-system-\S+)", clean)
                if m:
                    name = m.group(1)
            # Process start time → uptime
            stat_path = os.path.join(proc_dir, "stat")
            uptime_s = None
            try:
                stat = _read_file(stat_path)
                if stat:
                    fields = stat.rsplit(") ", 1)[-1].split()
                    # field 22 (0-indexed 21 after comm) is starttime in clock ticks
                    start_ticks = int(fields[19])
                    clk = os.sysconf("SC_CLK_TCK")
                    # System boot time
                    boot_file = _read_file("/proc/uptime")
                    if boot_file:
                        sys_up = float(boot_file.split()[0])
                        proc_age = sys_up - (start_ticks / clk)
                        uptime_s = int(max(0, proc_age))
            except (OSError, ValueError, IndexError):
                pass
            holders.append({
                "pid": pid,
                "name": name or "qemu",
                "uptime_s": uptime_s,
            })
    return holders


def status() -> dict:
    """Top-level audit : list each NVIDIA GPU, its driver state, and any
    qemu holders if VFIO-bound. Wrapped in a dict for handler consumption."""
    gpus = list_nvidia_gpus_with_state()
    vfio_bound_count = 0
    for gpu in gpus:
        if gpu["is_vfio"]:
            gpu["vm_holders"] = find_qemu_holders_for_bdf(gpu["bdf"])
            vfio_bound_count += 1
        else:
            gpu["vm_holders"] = []
    return {
        "ok": True,
        "available": True,
        "gpus_count": len(gpus),
        "vfio_bound_count": vfio_bound_count,
        "any_passthrough_active": vfio_bound_count > 0,
        "gpus": gpus,
    }
