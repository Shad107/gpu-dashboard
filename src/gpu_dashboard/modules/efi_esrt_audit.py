"""Module efi_esrt_audit — EFI System Resource Table audit
(R&D #67.1).

The **EFI System Resource Table (ESRT)** at
/sys/firmware/efi/esrt/ is the firmware-update inventory that
`fwupd`, `LVFS`, and `fwupdmgr` read to know which firmware
components on the system are updatable and what version they
sit at. Each entry exposes the result code of the last capsule
update attempt — a non-zero `last_attempt_status` means a recent
fwupd run failed silently, and the user usually doesn't know.

Why on a single-GPU homelab :

* Stale UEFI / BMC / NVMe / TPM firmware → real bugs (NVMe drive
  hangs, TPM PCR drift after kernel updates, IOMMU faults).
* fwupd silently retries after a failure — knowing the most
  recent `last_attempt_status` is the only way to notice you've
  been failing the same capsule install for months.
* Some boards omit ESRT entirely (older Proxmox-VM OVMF builds,
  legacy BIOS) — in that case fwupd can't help even if the user
  ran it.

Reads :
  /sys/firmware/efi/esrt/{fw_resource_count,
                          fw_resource_count_max,
                          fw_resource_version}
  /sys/firmware/efi/esrt/entries/entry*/{fw_class,
                                            fw_type,
                                            fw_version,
                                            lowest_supported_fw_version,
                                            capsule_flags,
                                            last_attempt_status,
                                            last_attempt_version}

last_attempt_status values (UEFI spec 2.10 §23.4) :
  0 = success ; non-zero = a fault code.

Verdicts (priority order) :
  last_capsule_failed         ≥1 entry has non-zero
                              last_attempt_status.
  stale_firmware_components   ≥1 entry has fw_version <
                              lowest_supported_fw_version.
  no_esrt_support             /sys/firmware/efi/esrt absent on
                              an otherwise-EFI host
                              (/sys/firmware/efi exists).
  esrt_empty                  ESRT present but
                              fw_resource_count == 0.
  ok                          All entries report success and
                              current fw ≥ lowest_supported.
  unknown                     /sys/firmware/efi absent (legacy
                              BIOS host).

stdlib only.
"""
from __future__ import annotations

import os
from typing import List, Optional


NAME = "efi_esrt_audit"


_SYS_EFI = "/sys/firmware/efi"
_SYS_ESRT = "/sys/firmware/efi/esrt"


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


def _read_hexlike(p: str) -> Optional[int]:
    """Many ESRT files store values as 0x... but some store plain
    integers. Tolerate both."""
    return _read_int(p)


def read_esrt_header(sys_esrt: str = _SYS_ESRT) -> dict:
    return {
        "fw_resource_count": _read_int(os.path.join(
            sys_esrt, "fw_resource_count")),
        "fw_resource_count_max": _read_int(os.path.join(
            sys_esrt, "fw_resource_count_max")),
        "fw_resource_version": _read_int(os.path.join(
            sys_esrt, "fw_resource_version")),
    }


def read_entry(entry_dir: str) -> dict:
    return {
        "id": os.path.basename(entry_dir),
        "fw_class": _read(os.path.join(entry_dir, "fw_class")),
        "fw_type": _read_int(os.path.join(entry_dir, "fw_type")),
        "fw_version": _read_hexlike(os.path.join(
            entry_dir, "fw_version")),
        "lowest_supported_fw_version": _read_hexlike(
            os.path.join(entry_dir,
                            "lowest_supported_fw_version")),
        "capsule_flags": _read_hexlike(os.path.join(
            entry_dir, "capsule_flags")),
        "last_attempt_status": _read_int(os.path.join(
            entry_dir, "last_attempt_status")),
        "last_attempt_version": _read_hexlike(os.path.join(
            entry_dir, "last_attempt_version")),
    }


def list_entries(sys_esrt: str = _SYS_ESRT) -> List[dict]:
    entries_dir = os.path.join(sys_esrt, "entries")
    if not os.path.isdir(entries_dir):
        return []
    out: List[dict] = []
    for name in sorted(os.listdir(entries_dir)):
        d = os.path.join(entries_dir, name)
        if not os.path.isdir(d):
            continue
        out.append(read_entry(d))
    return out


def classify(efi_present: bool, esrt_present: bool,
              header: dict, entries: List[dict]) -> dict:
    if not efi_present:
        return {"verdict": "unknown",
                "reason": ("/sys/firmware/efi absent — host "
                          "booted in legacy BIOS mode ; ESRT "
                          "not exposed."),
                "recommendation": _recipe_unknown_bios()}

    if efi_present and not esrt_present:
        return {"verdict": "no_esrt_support",
                "reason": ("EFI host detected but /sys/firmware/"
                          "efi/esrt is absent — UEFI firmware "
                          "does not publish an ESRT. fwupd cannot "
                          "discover updatable components."),
                "recommendation": _recipe_no_esrt()}

    rc = header.get("fw_resource_count")
    if rc == 0 and not entries:
        return {"verdict": "esrt_empty",
                "reason": ("ESRT exists but fw_resource_count = "
                          "0 — vendor publishes the table but "
                          "lists no updatable components."),
                "recommendation": _recipe_esrt_empty()}

    # 1) last_capsule_failed
    failed = [e for e in entries
                  if e.get("last_attempt_status") not in (0, None)]
    if failed:
        sample = ", ".join(
            f"{(e.get('fw_class') or e['id'])[:36]}="
            f"{e.get('last_attempt_status')}"
                for e in failed[:3])
        return {"verdict": "last_capsule_failed",
                "reason": (f"{len(failed)} ESRT entry/entries "
                          f"report non-zero "
                          f"last_attempt_status : {sample}."),
                "recommendation": _recipe_last_capsule_failed()}

    # 2) stale_firmware_components
    stale = []
    for e in entries:
        fwv = e.get("fw_version")
        lsv = e.get("lowest_supported_fw_version")
        if fwv is None or lsv is None:
            continue
        if lsv > 0 and fwv < lsv:
            stale.append(e)
    if stale:
        sample = ", ".join((s.get("fw_class") or s["id"])[:36]
                                for s in stale[:3])
        return {"verdict": "stale_firmware_components",
                "reason": (f"{len(stale)} firmware component(s) "
                          f"sit below the vendor's lowest_"
                          f"supported_fw_version : {sample}."),
                "recommendation": _recipe_stale_components()}

    return {"verdict": "ok",
            "reason": (f"ESRT present : {len(entries)} entry/"
                      f"entries, all last_attempt_status = 0 "
                      f"and current fw_version >= lowest_"
                      f"supported_fw_version."),
            "recommendation": ""}


def status(config=None,
            sys_efi: str = _SYS_EFI,
            sys_esrt: str = _SYS_ESRT) -> dict:
    efi_present = os.path.isdir(sys_efi)
    esrt_present = os.path.isdir(sys_esrt)
    header = read_esrt_header(sys_esrt) if esrt_present else {}
    entries = list_entries(sys_esrt) if esrt_present else []
    verdict = classify(efi_present, esrt_present, header, entries)
    return {"ok": efi_present,
              "efi_present": efi_present,
              "esrt_present": esrt_present,
              "fw_resource_count": header.get("fw_resource_count"),
              "fw_resource_count_max": header.get(
                  "fw_resource_count_max"),
              "fw_resource_version": header.get(
                  "fw_resource_version"),
              "entry_count": len(entries),
              "entries_sample": entries[:8],
              "verdict": verdict}


# ── recovery recipes ────────────────────────────────────────────

def _recipe_unknown_bios() -> str:
    return ("# This host booted in legacy BIOS mode — no ESRT.\n"
            "# To switch to UEFI : reinstall the OS in UEFI mode\n"
            "# OR run the BIOS / UEFI in legacy-and-UEFI hybrid.\n")


def _recipe_no_esrt() -> str:
    return ("# UEFI host but no ESRT means fwupd can't discover\n"
            "# capsule-updatable components. Common on :\n"
            "#  - OVMF / KVM virtual machines (no virt firmware)\n"
            "#  - Older boards (pre-2015) with UEFI but no ESRT\n"
            "# Check fwupd's own view :\n"
            "fwupdmgr get-devices --show-all\n")


def _recipe_esrt_empty() -> str:
    return ("# ESRT exists but vendor published zero entries.\n"
            "# Some boards ship empty ESRT until first BIOS\n"
            "# update is applied via vendor tool. Verify with :\n"
            "fwupdmgr get-devices\n")


def _recipe_last_capsule_failed() -> str:
    return ("# A capsule update failed silently. Inspect with :\n"
            "fwupdmgr get-history\n"
            "fwupdmgr get-devices --show-all\n"
            "# UEFI capsule status codes (UEFI spec 2.10 §23.4) :\n"
            "#  1 = unsuccessful\n"
            "#  2 = insufficient resources\n"
            "#  3 = incorrect version\n"
            "#  4 = invalid format\n"
            "#  5 = AC power not connected\n"
            "#  6 = insufficient battery\n")


def _recipe_stale_components() -> str:
    return ("# At least one firmware component is below vendor's\n"
            "# lowest_supported_fw_version. Run fwupd :\n"
            "fwupdmgr refresh --force\n"
            "fwupdmgr get-updates\n"
            "fwupdmgr update\n")
