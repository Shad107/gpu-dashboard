"""Module nvme_hmb_features_audit — NVMe Host Memory Buffer
feature posture (R&D #91.3).

The NVMe HMB feature lets DRAM-less SSDs use a slice of host
RAM as their FTL cache. Without it, a DRAM-less drive falls
back to a "no map" slow path and IOPS tanks 5x on random
small reads.

No existing module reads the HMB surface :

  * nvme_controller_state_audit (#86.3) reads controller
    state / firmware_rev / numa_node — not the HMB sysfs
    attr or the nvme.max_host_mem_size_mb module param.
  * nvme_iosched is I/O scheduler choice.
  * nvme_swap is swap-on-nvme posture.

Reads :

  /sys/class/nvme/nvme*/hmb                       bytes
  /sys/module/nvme/parameters/max_host_mem_size_mb cap (MiB)
                                                  0 = disabled
  /proc/meminfo                                   MemTotal

Verdicts (worst-first) :

  hmb_param_disabled_with_use  err   max_host_mem_size_mb = 0
                                     yet some controller is
                                     currently using HMB —
                                     param applies on next
                                     boot, but indicates
                                     intent to break HMB on
                                     reload.
  hmb_module_off_with_drives   warn  max_host_mem_size_mb = 0
                                     with NVMe drives present
                                     — DRAM-less SSDs will
                                     reload without HMB.
  hmb_oversized                accent HMB consuming > 64 MiB
                                     on a < 16 GiB box.
  ok                          HMB sized appropriately OR all
                              drives have on-board DRAM.
  requires_root               hmb file mode-600 (rare).
  unknown                     no /sys/class/nvme (no NVMe hw
                              or VM with virtio storage).

stdlib only.
"""
from __future__ import annotations

import os
import re
from typing import Optional

NAME = "nvme_hmb_features_audit"

DEFAULT_NVME_ROOT = "/sys/class/nvme"
DEFAULT_NVME_PARAM = (
    "/sys/module/nvme/parameters/max_host_mem_size_mb")
DEFAULT_MEMINFO = "/proc/meminfo"

# Accent threshold : > 64 MiB on small-RAM box.
_HMB_LARGE_BYTES = 64 * 2**20
_LOW_RAM_BYTES = 16 * 2**30

_MEMTOTAL_RE = re.compile(r"^MemTotal:\s*(\d+)\s*kB",
                          re.MULTILINE)


def _read_text(path: str) -> Optional[str]:
    try:
        with open(path, "r", encoding="utf-8") as fh:
            return fh.read().strip()
    except (OSError, PermissionError):
        return None


def _read_int(path: str) -> Optional[int]:
    t = _read_text(path)
    if t is None:
        return None
    try:
        return int(t)
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


def list_controllers(root: str = DEFAULT_NVME_ROOT) -> list:
    if not os.path.isdir(root):
        return []
    try:
        return sorted(
            n for n in os.listdir(root)
            if n.startswith("nvme") and n[4:].isdigit())
    except OSError:
        return []


def read_controller(root: str, name: str) -> dict:
    base = os.path.join(root, name)
    hmb_path = os.path.join(base, "hmb")
    hmb_text = _read_text(hmb_path)
    hmb_present = os.path.isfile(hmb_path)
    return {
        "name": name,
        "hmb_present": hmb_present,
        "hmb_readable": hmb_text is not None,
        "hmb_bytes": (
            int(hmb_text) if (hmb_text or "").isdigit()
            else None),
    }


def classify(controllers: list,
             max_host_mem_mb: Optional[int],
             mem_total: Optional[int]) -> dict:
    if not controllers:
        return {"verdict": "unknown",
                "reason": (
                    "No /sys/class/nvme/nvme* controllers — "
                    "no NVMe hardware visible (virtio storage "
                    "or kernel built without CONFIG_BLK_DEV_"
                    "NVME).")}

    # requires_root — any HMB file present but unreadable
    if any(c["hmb_present"] and not c["hmb_readable"]
           for c in controllers):
        return {"verdict": "requires_root",
                "reason": (
                    "/sys/class/nvme/nvme*/hmb present but "
                    "unreadable — re-run as root.")}

    in_use = [c for c in controllers
              if (c["hmb_bytes"] or 0) > 0]

    # err — param explicitly off but HMB is currently in use
    if max_host_mem_mb == 0 and in_use:
        names = [c["name"] for c in in_use]
        return {
            "verdict": "hmb_param_disabled_with_use",
            "reason": (
                "nvme.max_host_mem_size_mb = 0 but "
                f"{len(in_use)} controller(s) currently use "
                f"HMB: {names}. Reload will drop HMB and may "
                "tank IOPS on DRAM-less SSDs."),
            "controllers": names}

    # warn — param off with drives present
    if max_host_mem_mb == 0 and controllers:
        names = [c["name"] for c in controllers]
        return {
            "verdict": "hmb_module_off_with_drives",
            "reason": (
                "nvme.max_host_mem_size_mb = 0 with "
                f"{len(controllers)} NVMe controller(s) "
                f"present {names}. Any DRAM-less drives will "
                "operate without HMB cache (5x slower random "
                "small reads)."),
            "controllers": names}

    # accent — HMB oversized on small-RAM box
    if mem_total is not None and mem_total < _LOW_RAM_BYTES:
        total_hmb = sum(c["hmb_bytes"] or 0
                        for c in controllers)
        if total_hmb > _HMB_LARGE_BYTES:
            return {
                "verdict": "hmb_oversized",
                "reason": (
                    f"HMB consuming {total_hmb / 2**20:.1f} "
                    f"MiB across {len(in_use)} controller(s) "
                    f"on a {mem_total / 2**30:.0f} GiB box "
                    "— meaningful RAM tax."),
                "hmb_bytes": total_hmb}

    return {"verdict": "ok",
            "reason": (
                f"{len(controllers)} NVMe controller(s) ; "
                f"{len(in_use)} using HMB ; param "
                f"max_host_mem_size_mb = "
                f"{max_host_mem_mb if max_host_mem_mb is not None else '?'}"
                ".")}


def status(config: Optional[dict] = None,
           nvme_root: str = DEFAULT_NVME_ROOT,
           param_path: str = DEFAULT_NVME_PARAM,
           meminfo_path: str = DEFAULT_MEMINFO) -> dict:
    names = list_controllers(nvme_root)
    controllers = [read_controller(nvme_root, n) for n in names]
    max_host_mem_mb = _read_int(param_path)
    mem_total = parse_meminfo_total_bytes(
        _read_text(meminfo_path) or "")
    verdict = classify(controllers, max_host_mem_mb, mem_total)
    return {
        "ok": verdict["verdict"] == "ok",
        "controller_count": len(controllers),
        "hmb_using_count": sum(
            1 for c in controllers
            if (c["hmb_bytes"] or 0) > 0),
        "max_host_mem_size_mb": max_host_mem_mb,
        "verdict": verdict,
    }
