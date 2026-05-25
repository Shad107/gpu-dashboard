"""Module acpi_boot_assets_audit — BGRT + FPDT boot-asset
posture (R&D #109.2).

ACPI exposes two boot-time assets the kernel makes visible
under /sys/firmware/acpi/:

  bgrt/                 Boot Graphics Resource Table (the
                        firmware-rendered logo). status bit 0
                        tells us whether firmware actually
                        displayed it.
  fpdt/                 Firmware Performance Data Table —
                        boot timing decomposition. Absent on
                        most non-Lenovo/Dell laptops + VMs.

acpi_audit lists these paths in its docstring but its actual
readers cover only platform_profile, pm_profile, interrupts/gpe,
wakeup — never bgrt or fpdt (grep-verified).

Reads :

  /sys/firmware/acpi/bgrt/status        bit 0 = displayed
  /sys/firmware/acpi/bgrt/type          0 = BMP, 1 = PNG
  /sys/firmware/acpi/bgrt/version
  /sys/firmware/acpi/fpdt/              presence only

Verdicts (worst-first) :

  bgrt_status_invalid    warn    BGRT exists but status bit 0
                                 clear — firmware didn't
                                 validly display the boot
                                 graphic. Often signals a
                                 borked SecureBoot chain or
                                 quickboot misconfig.
  no_boot_assets         accent  Neither BGRT nor FPDT exposed
                                 — firmware doesn't ship
                                 either. Common on VMs.
  ok                             BGRT valid OR firmware just
                                 doesn't have them.
  requires_root                  sysfs unreadable.
  unknown                        /sys/firmware/acpi absent.

stdlib only.
"""
from __future__ import annotations

import os
from typing import Optional

NAME = "acpi_boot_assets_audit"

DEFAULT_ACPI_ROOT = "/sys/firmware/acpi"


def _read_int(path: str) -> Optional[int]:
    try:
        with open(path, "r", encoding="utf-8") as fh:
            return int(fh.read().strip())
    except (OSError, PermissionError, ValueError):
        return None


def classify(acpi_present: bool,
             bgrt_present: bool,
             bgrt_status: Optional[int],
             bgrt_type: Optional[int],
             fpdt_present: bool) -> dict:
    if not acpi_present:
        return {"verdict": "unknown",
                "reason": (
                    "/sys/firmware/acpi absent — no ACPI "
                    "subsystem (non-ACPI kernel or "
                    "uncommon host).")}

    if bgrt_present and bgrt_status is None:
        return {"verdict": "requires_root",
                "reason": (
                    "BGRT status unreadable — re-run as "
                    "root.")}

    # warn — BGRT exists but invalid
    if (bgrt_present and bgrt_status is not None
            and (bgrt_status & 1) == 0):
        return {
            "verdict": "bgrt_status_invalid",
            "reason": (
                f"BGRT.status={bgrt_status} (bit 0 clear) "
                "— firmware didn't validly display the boot "
                "graphic. Possibly broken SecureBoot chain "
                "or quickboot misconfig.")}

    # accent — neither asset present
    if not bgrt_present and not fpdt_present:
        return {
            "verdict": "no_boot_assets",
            "reason": (
                "Neither BGRT nor FPDT exposed by firmware "
                "— common on VMs and minimal-firmware boards "
                "; informational only.")}

    return {"verdict": "ok",
            "reason": (
                f"bgrt={bgrt_present} (status={bgrt_status}, "
                f"type={bgrt_type}) ; "
                f"fpdt={fpdt_present}. Sane.")}


def status(config: Optional[dict] = None,
           acpi_root: str = DEFAULT_ACPI_ROOT) -> dict:
    acpi_present = os.path.isdir(acpi_root)
    bgrt_dir = os.path.join(acpi_root, "bgrt")
    fpdt_dir = os.path.join(acpi_root, "fpdt")
    bgrt_present = os.path.isdir(bgrt_dir)
    fpdt_present = os.path.isdir(fpdt_dir)
    bgrt_status = (_read_int(os.path.join(bgrt_dir, "status"))
                   if bgrt_present else None)
    bgrt_type = (_read_int(os.path.join(bgrt_dir, "type"))
                 if bgrt_present else None)
    verdict = classify(acpi_present, bgrt_present,
                       bgrt_status, bgrt_type,
                       fpdt_present)
    return {
        "ok": verdict["verdict"] == "ok",
        "bgrt_present": bgrt_present,
        "bgrt_status": bgrt_status,
        "bgrt_type": bgrt_type,
        "fpdt_present": fpdt_present,
        "verdict": verdict,
    }
