"""Module dmi_entries_raw_audit — DMI / SMBIOS raw-entries
table audit (R&D #72.2).

Existing dmi_smbios_audit reads the decoded /sys/class/dmi/id/*
strings (sys_vendor, product_name, board_serial …). This audit
goes one level deeper : it enumerates /sys/firmware/dmi/entries/
which is the kernel's raw per-record table.

Directory naming convention :
  /sys/firmware/dmi/entries/<TYPE>-<INSTANCE>/
where TYPE is the SMBIOS structure-type code (1 = System,
4 = Processor, 9 = System Slot, 16 = Physical Memory Array,
17 = Memory Device, 19 = Memory Array Mapped Address,
32 = System Boot Info, 38 = IPMI Device, 127 = End of table).

Crucially the *directory names* expose this classification
without requiring root — the {raw,type,handle,length} files
themselves are 0400 root.

Why on a homelab :

* **Type 38 (IPMI Device) present** on a consumer board is a
  giveaway of an exposed BMC (Aspeed, etc.) management
  interface — security-relevant.
* **dimm_slot_mismatch** : Type 17 (Memory Device) count and
  Type 9 (System Slot) count should agree on a real motherboard ;
  in QEMU / Proxmox guests they diverge silently.
* **smbios_truncated** : fewer than ~6 distinct types means the
  vendor's firmware or hypervisor cut the table short, breaking
  fwupd / lshw / dmidecode results.

Verdicts (priority order) :
  ipmi_bmc_exposed        ≥1 Type-38 (IPMI Device) entry.
  dimm_slot_mismatch      Type-17 (Memory Device) count !=
                           Type-9 (System Slot) count AND both
                           > 0.
  smbios_truncated        Fewer than 6 distinct DMI types
                           enumerated.
  requires_root           Entries directory absent OR listing
                           denied AND no type-from-dirname info.
  ok                      DMI table healthy, no IPMI exposure.
  unknown                 /sys/firmware/dmi/entries absent.

stdlib only.
"""
from __future__ import annotations

import os
import re
from collections import Counter
from typing import Dict, List, Optional


NAME = "dmi_entries_raw_audit"


_SYS_DMI_ENTRIES = "/sys/firmware/dmi/entries"


# Directory name = "<TYPE>-<INSTANCE>"
_ENTRY_RE = re.compile(r"^(\d+)-(\d+)$")


# SMBIOS type → human label (covers the common ones)
_TYPE_LABELS = {
    0: "BIOS",
    1: "System",
    2: "Baseboard",
    3: "Chassis",
    4: "Processor",
    7: "Cache",
    9: "System Slot",
    11: "OEM Strings",
    13: "BIOS Language",
    16: "Physical Memory Array",
    17: "Memory Device",
    19: "Memory Array Mapped Address",
    20: "Memory Device Mapped Address",
    32: "System Boot Info",
    38: "IPMI Device",
    127: "End of Table",
}


_TRUNCATED_TYPE_FLOOR = 6


def _read(path: str) -> Optional[str]:
    try:
        with open(path) as f:
            return f.read().strip()
    except OSError:
        return None


def _read_int(path: str) -> Optional[int]:
    t = _read(path)
    if t is None:
        return None
    try:
        return int(t)
    except ValueError:
        return None


def list_entries(sys_path: str = _SYS_DMI_ENTRIES
                      ) -> List[dict]:
    """Returns one dict per entry directory.

    Type is parsed from the directory name (always readable
    without root) ; the metadata files inside are 0400 so we
    only attempt them best-effort."""
    if not os.path.isdir(sys_path):
        return []
    try:
        names = sorted(os.listdir(sys_path))
    except OSError:
        return []
    out: List[dict] = []
    for n in names:
        d = os.path.join(sys_path, n)
        if not os.path.isdir(d):
            continue
        m = _ENTRY_RE.match(n)
        if not m:
            continue
        ty = int(m.group(1))
        instance = int(m.group(2))
        out.append({
            "id": n,
            "type": ty,
            "type_label": _TYPE_LABELS.get(ty, "unknown"),
            "instance": instance,
            "handle": _read_int(os.path.join(d, "handle")),
            "length": _read_int(os.path.join(d, "length")),
            "type_readable":
                _read(os.path.join(d, "type")) is not None,
        })
    return out


def classify(entries: List[dict], path_present: bool,
              listable: bool) -> dict:
    if not path_present:
        return {"verdict": "unknown",
                "reason": ("/sys/firmware/dmi/entries absent — "
                          "kernel without CONFIG_DMI or running "
                          "in a non-DMI guest."),
                "recommendation": ""}

    if not listable or not entries:
        return {"verdict": "requires_root",
                "reason": ("/sys/firmware/dmi/entries present "
                          "but no entries enumerable (listing "
                          "denied or empty)."),
                "recommendation": _recipe_requires_root()}

    type_counts = Counter(e["type"] for e in entries)
    distinct_types = len(type_counts)

    # 1) ipmi_bmc_exposed
    if type_counts.get(38, 0) > 0:
        return {"verdict": "ipmi_bmc_exposed",
                "reason": (f"DMI Type 38 (IPMI Device) is "
                          f"exposed via SMBIOS "
                          f"({type_counts[38]} instance(s)). "
                          f"Confirm the BMC is intended on this "
                          f"machine."),
                "recommendation": _recipe_ipmi()}

    # 2) dimm_slot_mismatch
    n_dimm = type_counts.get(17, 0)
    n_slot = type_counts.get(9, 0)
    if n_dimm > 0 and n_slot > 0 and n_dimm != n_slot:
        return {"verdict": "dimm_slot_mismatch",
                "reason": (f"DMI Type 17 (Memory Device) count "
                          f"= {n_dimm} but Type 9 (System Slot) "
                          f"count = {n_slot} — should agree on "
                          f"a real motherboard."),
                "recommendation": _recipe_slot_mismatch()}

    # 3) smbios_truncated
    if distinct_types < _TRUNCATED_TYPE_FLOOR:
        return {"verdict": "smbios_truncated",
                "reason": (f"Only {distinct_types} distinct DMI "
                          f"types enumerated (floor "
                          f"{_TRUNCATED_TYPE_FLOOR}). Vendor "
                          f"firmware / hypervisor may have "
                          f"truncated the SMBIOS table."),
                "recommendation": _recipe_truncated()}

    return {"verdict": "ok",
            "reason": (f"{len(entries)} entries across "
                      f"{distinct_types} distinct types ; "
                      f"DIMMs={n_dimm}, slots={n_slot}, "
                      f"no IPMI exposure."),
            "recommendation": ""}


def status(config=None,
            sys_path: str = _SYS_DMI_ENTRIES) -> dict:
    path_present = os.path.isdir(sys_path)
    listable = False
    if path_present:
        try:
            os.listdir(sys_path)
            listable = True
        except OSError:
            listable = False
    entries = list_entries(sys_path) if listable else []
    type_counts: Dict[int, int] = {}
    for e in entries:
        type_counts[e["type"]] = type_counts.get(
            e["type"], 0) + 1
    verdict = classify(entries, path_present, listable)
    return {"ok": path_present,
              "path_present": path_present,
              "listable": listable,
              "entry_count": len(entries),
              "distinct_type_count": len(type_counts),
              "type_counts": {str(k): v
                                  for k, v in sorted(
                                      type_counts.items())},
              "entries_sample": entries[:10],
              "verdict": verdict}


# ── recovery recipes ────────────────────────────────────────────

def _recipe_requires_root() -> str:
    return ("# Type/handle files are 0400. The directory naming\n"
            "# convention <TYPE>-<INSTANCE> is enough for most\n"
            "# audits. To read the raw SMBIOS blob :\n"
            "sudo dmidecode --type 0,1,4,17,38\n")


def _recipe_ipmi() -> str:
    return ("# IPMI Device exposed via SMBIOS — implies a BMC.\n"
            "# Confirm intended :\n"
            "sudo dmidecode -t 38\n"
            "# If the BMC is not used, disable IPMI-over-LAN :\n"
            "sudo ipmitool lan set 1 access off\n"
            "# Audit BMC firmware version :\n"
            "sudo ipmitool mc info\n")


def _recipe_slot_mismatch() -> str:
    return ("# Type 17 vs Type 9 count mismatch :\n"
            "sudo dmidecode -t memory | grep -E 'Memory Device|"
            "System Slot' -A2\n"
            "# Real boards report one Type 9 per physical slot.\n"
            "# Virt guests routinely omit one or the other.\n")


def _recipe_truncated() -> str:
    return ("# Few DMI types enumerated. Inspect raw table size :\n"
            "sudo dmidecode | head\n"
            "# Compare against vendor's expected layout :\n"
            "sudo dmidecode --type 0\n")
