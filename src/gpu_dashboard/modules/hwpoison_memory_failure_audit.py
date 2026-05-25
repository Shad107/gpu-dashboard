"""Module hwpoison_memory_failure_audit — kernel hwpoison /
memory_failure surface (R&D #94.1).

Three existing modules touch related HW-error surface but
none read the hwpoison subsystem :

  * edac_ecc_audit          — EDAC controller CE counters
  * edac_dimm_ce_trend_audit — EDAC DIMM rates
  * mce_audit               — /var/log/mcelog / MCE parse
  * retired_pages           — NVIDIA GPU VRAM retirement

The kernel's own hwpoison machinery exposes the physical
DRAM pages it has retired due to ECC errors. The total
appears in /proc/meminfo as 'HardwareCorrupted'. Non-zero
means real bit-rot has already occurred — and the user's
next dataloader segfault is probably hitting a retired page.

Reads :

  /proc/meminfo                       'HardwareCorrupted: N kB'
  /proc/vmstat                        hwpoison_pages_failed,
                                      memory_failure_*
  /sys/devices/system/edac            EDAC presence indicator
                                      (cross-ref for accent
                                      verdict).

Verdicts (worst-first) :

  hwpoison_active             err   HardwareCorrupted > 0 —
                                    DRAM bit-rot has retired
                                    physical pages.
  hwpoison_failed_recoveries  warn  vmstat hwpoison_pages_
                                    failed > 0 or any
                                    memory_failure_*_failed
                                    counter > 0 — kernel
                                    tried + couldn't isolate
                                    a poisoned page.
  edac_present_no_hwpoison    accent EDAC controllers exist
                                    but kernel built without
                                    CONFIG_MEMORY_FAILURE
                                    (HardwareCorrupted field
                                    missing in meminfo).
  ok                          HardwareCorrupted = 0, no
                              failed recoveries.
  unknown                     /proc/meminfo unreadable.

stdlib only.
"""
from __future__ import annotations

import os
import re
from typing import Optional

NAME = "hwpoison_memory_failure_audit"

DEFAULT_PROC_MEMINFO = "/proc/meminfo"
DEFAULT_PROC_VMSTAT = "/proc/vmstat"
DEFAULT_SYS_EDAC = "/sys/devices/system/edac"

_HARDWARE_CORRUPTED_RE = re.compile(
    r"^HardwareCorrupted:\s*(\d+)\s*kB", re.MULTILINE)


def _read_text(path: str) -> Optional[str]:
    try:
        with open(path, "r", encoding="utf-8") as fh:
            return fh.read()
    except (OSError, PermissionError):
        return None


def parse_hardware_corrupted_kib(text: str) -> Optional[int]:
    """Returns kB if field present, None if absent."""
    if not text:
        return None
    m = _HARDWARE_CORRUPTED_RE.search(text)
    if not m:
        return None
    try:
        return int(m.group(1))
    except ValueError:
        return None


def parse_hwpoison_vmstat(text: str) -> dict:
    """Return all hwpoison_* + memory_failure_* counters."""
    out: dict = {}
    if not text:
        return out
    for line in text.splitlines():
        parts = line.split()
        if len(parts) != 2:
            continue
        if (parts[0].startswith("hwpoison_")
                or parts[0].startswith("memory_failure_")):
            try:
                out[parts[0]] = int(parts[1])
            except ValueError:
                continue
    return out


def edac_present(root: str = DEFAULT_SYS_EDAC) -> bool:
    if not os.path.isdir(root):
        return False
    try:
        entries = os.listdir(root)
    except OSError:
        return False
    # 'mc' is the standard EDAC mc subsystem dir.
    return any(e.startswith("mc") for e in entries)


def classify(hw_corrupted_kib: Optional[int],
             hwpoison: dict,
             edac: bool) -> dict:
    if hw_corrupted_kib is None:
        if edac:
            return {
                "verdict": "edac_present_no_hwpoison",
                "reason": (
                    "EDAC memory controllers present in "
                    "/sys/devices/system/edac but "
                    "/proc/meminfo lacks "
                    "'HardwareCorrupted' — kernel built "
                    "without CONFIG_MEMORY_FAILURE. Page-"
                    "level isolation of ECC errors is off."),
            }
        return {"verdict": "unknown",
                "reason": (
                    "/proc/meminfo HardwareCorrupted field "
                    "absent — kernel built without "
                    "CONFIG_MEMORY_FAILURE.")}

    # err — any bit-rot already retired pages
    if hw_corrupted_kib > 0:
        return {
            "verdict": "hwpoison_active",
            "reason": (
                f"HardwareCorrupted = {hw_corrupted_kib} kB "
                "in /proc/meminfo — DRAM bit-rot has retired "
                "physical pages. Replace failing DIMMs."),
            "kib": hw_corrupted_kib}

    # warn — failed recoveries
    failed = sum(
        v for k, v in hwpoison.items() if "fail" in k)
    if failed > 0:
        sample = {k: v for k, v in hwpoison.items()
                  if "fail" in k and v > 0}
        return {
            "verdict": "hwpoison_failed_recoveries",
            "reason": (
                f"{failed} failed memory-failure recoveries "
                f"across vmstat counters ({sample}). Kernel "
                "tried to isolate poisoned pages and "
                "couldn't — risk of soft-corruption "
                "reaching userspace."),
            "failed_count": failed}

    return {"verdict": "ok",
            "reason": (
                f"HardwareCorrupted = 0 kB ; "
                f"{len(hwpoison)} hwpoison/"
                "memory_failure vmstat counter(s), all "
                "zero or healthy.")}


def status(config: Optional[dict] = None,
           meminfo_path: str = DEFAULT_PROC_MEMINFO,
           vmstat_path: str = DEFAULT_PROC_VMSTAT,
           edac_root: str = DEFAULT_SYS_EDAC) -> dict:
    hw_corrupted = parse_hardware_corrupted_kib(
        _read_text(meminfo_path) or "")
    hwpoison = parse_hwpoison_vmstat(
        _read_text(vmstat_path) or "")
    edac = edac_present(edac_root)
    verdict = classify(hw_corrupted, hwpoison, edac)
    return {
        "ok": verdict["verdict"] == "ok",
        "hardware_corrupted_kib": hw_corrupted,
        "hwpoison_counter_count": len(hwpoison),
        "edac_present": edac,
        "verdict": verdict,
    }
