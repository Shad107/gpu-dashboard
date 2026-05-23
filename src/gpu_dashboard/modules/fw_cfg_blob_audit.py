"""Module fw_cfg_blob_audit — QEMU fw_cfg blob inventory
(R&D #72.1).

QEMU exposes guest-side configuration to the kernel via the
`qemu_fw_cfg` interface mounted at /sys/firmware/qemu_fw_cfg/.
Each entry is a (key, name, blob) tuple ; well-known keys
expose the SMBIOS table, e820 map, ACPI tables, kernel cmdline,
opt-rom blobs for passed-through devices, and vendor-specific
extras.

For a homelab :

* Detecting a QEMU guest is already done by other modules
  (virt_guest_detect / host_class) on presence basis. This
  audit goes deeper : enumerate the entries to surface
  *what kind* of QEMU guest — bare (defaults) vs passthrough
  (opt-rom blobs imply VFIO / GPU passthrough) vs custom
  (etc/ entries from libvirt domain XML).
* Names are 0400 root on most kernels (KASLR / leak avoidance)
  ; the audit reports a degraded `requires_root` verdict
  rather than crash when the names can't be read.

Reads :
  /sys/firmware/qemu_fw_cfg/rev                  fw_cfg rev int
  /sys/firmware/qemu_fw_cfg/by_key/<n>/name      entry name
  /sys/firmware/qemu_fw_cfg/by_key/<n>/size      entry blob size

Verdicts (priority order) :
  nvidia_passthrough_vm     ≥1 entry name matches a known
                              vendor-rom pattern
                              (genroms/ + 10de:* PCI ID).
  qemu_guest_with_opt_rom   ≥1 entry name matches
                              "genroms/" or "opt/com.redhat/"
                              (custom passthrough).
  qemu_guest_bare           /sys/firmware/qemu_fw_cfg present
                              with default-only entries.
  requires_root             fw_cfg present but entry names
                              unreadable.
  ok                        not a QEMU guest.
  unknown                   indeterminate.

stdlib only.
"""
from __future__ import annotations

import os
import re
from typing import List, Optional


NAME = "fw_cfg_blob_audit"


_SYS_FW_CFG = "/sys/firmware/qemu_fw_cfg"
_FW_CFG_BY_KEY = "/sys/firmware/qemu_fw_cfg/by_key"


_NVIDIA_ROM_RE = re.compile(
    r"genroms/.*(?:10de|nvidia)", re.IGNORECASE)
_OPT_ROM_RE = re.compile(
    r"(?:genroms/|opt/com\.redhat/)", re.IGNORECASE)


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


def list_entries(by_key: str = _FW_CFG_BY_KEY) -> List[dict]:
    if not os.path.isdir(by_key):
        return []
    out: List[dict] = []
    try:
        names = sorted(os.listdir(by_key), key=lambda n:
                          int(n) if n.isdigit() else 999999)
    except OSError:
        return []
    for k in names:
        if not k.isdigit():
            continue
        d = os.path.join(by_key, k)
        if not os.path.isdir(d):
            continue
        out.append({
            "key": int(k),
            "name": _read(os.path.join(d, "name")),
            "size": _read_int(os.path.join(d, "size")),
        })
    return out


def classify(present: bool, entries: List[dict]) -> dict:
    if not present:
        return {"verdict": "ok",
                "reason": ("/sys/firmware/qemu_fw_cfg absent — "
                          "host is not a QEMU guest."),
                "recommendation": ""}

    if not entries:
        return {"verdict": "unknown",
                "reason": ("/sys/firmware/qemu_fw_cfg present "
                          "but no entries discoverable."),
                "recommendation": ""}

    name_readable = any(e.get("name") is not None
                              for e in entries)

    if not name_readable:
        return {"verdict": "requires_root",
                "reason": (f"QEMU guest detected ({len(entries)}"
                          f" fw_cfg entries) but entry names "
                          f"are 0400 root-only — cannot classify "
                          f"the passthrough profile."),
                "recommendation": _recipe_requires_root()}

    # 1) nvidia_passthrough_vm
    nvidia = [e for e in entries
                  if e.get("name")
                  and _NVIDIA_ROM_RE.search(e["name"])]
    if nvidia:
        sample = ", ".join(e["name"] for e in nvidia[:3])
        return {"verdict": "nvidia_passthrough_vm",
                "reason": (f"{len(nvidia)} fw_cfg entries point "
                          f"to NVIDIA opt-ROM blobs : {sample}."),
                "recommendation": _recipe_nvidia()}

    # 2) qemu_guest_with_opt_rom
    opt_rom = [e for e in entries
                  if e.get("name")
                  and _OPT_ROM_RE.search(e["name"])]
    if opt_rom:
        sample = ", ".join(e["name"] for e in opt_rom[:3])
        return {"verdict": "qemu_guest_with_opt_rom",
                "reason": (f"{len(opt_rom)} fw_cfg entries are "
                          f"custom opt-ROMs / libvirt extras : "
                          f"{sample}."),
                "recommendation": _recipe_opt_rom()}

    # 3) qemu_guest_bare
    return {"verdict": "qemu_guest_bare",
            "reason": (f"QEMU guest with {len(entries)} default "
                      f"fw_cfg entries and no opt-ROMs."),
            "recommendation": ""}


def status(config=None,
            sys_fw_cfg: str = _SYS_FW_CFG,
            by_key: str = _FW_CFG_BY_KEY) -> dict:
    present = os.path.isdir(sys_fw_cfg)
    entries = list_entries(by_key)
    name_readable = any(e.get("name") is not None
                              for e in entries) if entries else False
    rev = _read_int(os.path.join(sys_fw_cfg, "rev"))
    verdict = classify(present, entries)
    return {"ok": present,
              "present": present,
              "rev": rev,
              "entry_count": len(entries),
              "names_readable": name_readable,
              "entries_sample": entries[:8],
              "verdict": verdict}


# ── recovery recipes ────────────────────────────────────────────

def _recipe_requires_root() -> str:
    return ("# fw_cfg entry names are root-only on most kernels.\n"
            "# Enumerate as root :\n"
            "for k in /sys/firmware/qemu_fw_cfg/by_key/*; do\n"
            "  echo \"$(basename $k) -> $(sudo cat $k/name)\"\n"
            "done | sort -n | head -30\n")


def _recipe_nvidia() -> str:
    return ("# NVIDIA passthrough VM detected. Recipes :\n"
            "lspci -nnk | grep -A2 -i nvidia\n"
            "# Confirm VFIO :\n"
            "lsmod | grep vfio\n"
            "# Inspect opt-ROM blob size :\n"
            "sudo cat /sys/firmware/qemu_fw_cfg/by_key/*/size\n")


def _recipe_opt_rom() -> str:
    return ("# Custom opt-ROM / libvirt extras present. Inspect :\n"
            "for k in /sys/firmware/qemu_fw_cfg/by_key/*; do\n"
            "  n=$(sudo cat $k/name 2>/dev/null)\n"
            "  case \"$n\" in genroms/*|opt/*) echo \"$n\" ;; esac\n"
            "done\n")
