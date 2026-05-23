"""Module dmi_smbios_audit — DMI / SMBIOS metadata (R&D #59.1).

Reads /sys/class/dmi/id/* for vendor / board / BIOS metadata.
Surfaces three real LLM-host concerns :

* Stale BIOS (> 3 years) on a homelab box silently caps PCIe Gen4
  negotiation, lacks Resizable BAR support, or misses AGESA
  microcode fixes for Ryzen idle voltage and RTX 3090 link
  retraining.
* QEMU / KVM virt detection — many sysfs/hwmon/RAPL modules
  legitimately surface 'unknown' on a VM ; this verdict
  classifies the host so the user can interpret 'unknown' rows
  correctly.
* DMI subsystem absent (rare — Pi / ARM SBC) — alternative info
  paths needed.

Reads :
  /sys/class/dmi/id/{sys_vendor, product_name, product_family,
                       product_version, board_vendor, board_name,
                       board_version, bios_vendor, bios_version,
                       bios_date, bios_release, chassis_type,
                       chassis_vendor}

Verdicts (priority-ordered) :
  dmi_absent                  /sys/class/dmi/id missing or empty.
  bios_stale_gt_3y            bios_date older than 3 years.
  qemu_or_vm_detected         sys_vendor matches QEMU / VMware /
                              KVM / Xen / Microsoft / innotek
                              GmbH / Bochs.
  board_unknown               sys_vendor or board_vendor missing.
  ok                          metadata present, BIOS within 3
                              years, bare-metal host.
  unknown                     fallback.

stdlib only.
"""
from __future__ import annotations

import datetime
import os
from typing import Optional


NAME = "dmi_smbios_audit"


_SYS_DMI_ID = "/sys/class/dmi/id"


_VM_VENDORS = (
    "qemu", "kvm", "vmware", "xen", "microsoft",
    "innotek gmbh", "bochs", "google", "amazon ec2",
)


def _read(p: str) -> Optional[str]:
    try:
        with open(p) as f:
            return f.read().strip()
    except OSError:
        return None


def read_dmi(sys_dmi_id: str = _SYS_DMI_ID) -> dict:
    """Returns dict of DMI fields ; absent fields → None."""
    if not os.path.isdir(sys_dmi_id):
        return {}
    fields = (
        "sys_vendor", "product_name", "product_family",
        "product_version", "board_vendor", "board_name",
        "board_version", "bios_vendor", "bios_version",
        "bios_date", "bios_release", "chassis_type",
        "chassis_vendor",
    )
    return {f: _read(os.path.join(sys_dmi_id, f)) for f in fields}


def parse_bios_date(s: Optional[str]) -> Optional[datetime.date]:
    """DMI bios_date is reported as MM/DD/YYYY by the kernel."""
    if not s:
        return None
    try:
        return datetime.datetime.strptime(
            s.strip(), "%m/%d/%Y").date()
    except ValueError:
        return None


def is_vm_vendor(vendor: Optional[str]) -> bool:
    if not vendor:
        return False
    v = vendor.lower()
    return any(v.startswith(t) or t in v for t in _VM_VENDORS)


def classify(dmi: dict,
              today: Optional[datetime.date] = None) -> dict:
    if not dmi:
        return {"verdict": "dmi_absent",
                "reason": ("/sys/class/dmi/id is absent — no SMBIOS "
                          "metadata (typical on a Pi / ARM SBC)."),
                "recommendation": _recipe_alt_metadata()}

    if today is None:
        today = datetime.date.today()

    # 1) bios_stale_gt_3y
    bios_date = parse_bios_date(dmi.get("bios_date"))
    if bios_date is not None:
        age_days = (today - bios_date).days
        if age_days > 3 * 365:
            years = age_days / 365.25
            return {"verdict": "bios_stale_gt_3y",
                    "reason": (f"BIOS dated "
                              f"{bios_date.isoformat()} — "
                              f"{years:.1f} years old. Likely "
                              f"missing AGESA / Resizable BAR / "
                              f"PCIe Gen4 fixes."),
                    "recommendation": _recipe_bios_update()}

    # 2) qemu_or_vm_detected
    if is_vm_vendor(dmi.get("sys_vendor")):
        return {"verdict": "qemu_or_vm_detected",
                "reason": (f"sys_vendor = "
                          f"'{dmi.get('sys_vendor')}'. Many sysfs "
                          f"observability paths legitimately "
                          f"surface 'unknown' on this VM."),
                "recommendation": _recipe_vm_acknowledged()}

    # 3) board_unknown
    if not dmi.get("sys_vendor") and not dmi.get("board_vendor"):
        return {"verdict": "board_unknown",
                "reason": ("Both sys_vendor and board_vendor are "
                          "empty — vendor stripped SMBIOS strings "
                          "for privacy."),
                "recommendation": _recipe_smbios_strip()}

    return {"verdict": "ok",
            "reason": (f"{dmi.get('sys_vendor', '?')} / "
                      f"{dmi.get('product_name', '?')} — BIOS "
                      f"{dmi.get('bios_version', '?')} dated "
                      f"{dmi.get('bios_date', '?')}."),
            "recommendation": ""}


def status(config=None,
            sys_dmi_id: str = _SYS_DMI_ID,
            today: Optional[datetime.date] = None) -> dict:
    dmi = read_dmi(sys_dmi_id)
    ok = bool(dmi)
    verdict = classify(dmi, today)
    return {"ok": ok,
              "dmi": dmi,
              "is_vm": is_vm_vendor(dmi.get("sys_vendor")),
              "verdict": verdict}


# ── recovery recipes ────────────────────────────────────────────

def _recipe_alt_metadata() -> str:
    return ("# DMI is absent — use vendor-specific paths instead :\n"
            "cat /proc/device-tree/model 2>/dev/null  # ARM/Pi\n"
            "cat /sys/firmware/devicetree/base/compatible 2>/dev/null\n"
            "dmesg | grep -i 'machine model\\|product'\n")


def _recipe_bios_update() -> str:
    return ("# Check vendor support page for a newer BIOS / AGESA :\n"
            "cat /sys/class/dmi/id/{sys_vendor,product_name,bios_*}\n"
            "# Recent boards support live flashing via fwupd :\n"
            "fwupdmgr refresh && fwupdmgr update\n"
            "# Look up the board's release notes for resizable-BAR\n"
            "# and PCIe Gen4 stability fixes specifically.\n")


def _recipe_vm_acknowledged() -> str:
    return ("# Running in a VM — some sysfs observability paths\n"
            "# (RAPL, hwmon, EDAC, NUMA hardware) are intentionally\n"
            "# absent. Treat 'unknown' verdicts on those modules\n"
            "# as VM-correct, not a real issue.\n")


def _recipe_smbios_strip() -> str:
    return ("# Vendor stripped SMBIOS strings — check via dmidecode :\n"
            "sudo dmidecode -s system-manufacturer\n"
            "sudo dmidecode -s baseboard-manufacturer\n"
            "# Or read raw DMI table :\n"
            "sudo cat /sys/firmware/dmi/tables/DMI | strings | head\n")
