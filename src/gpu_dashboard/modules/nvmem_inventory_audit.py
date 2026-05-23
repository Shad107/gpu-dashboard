"""Module nvmem_inventory_audit — NVMEM device inventory
(R&D #69.1).

The Linux NVMEM subsystem at /sys/bus/nvmem/devices/ exposes
non-volatile memory regions : board EEPROMs, factory OTP fuses,
TPM NVRAM, BMC SDR caches, CMOS battery RAM. Each device has a
binary blob file whose POSIX permissions determine who can read
the contents.

On vendor-tweaked hardware (Lenovo, Dell, some ASUS boards) the
factory OTP / fuse blob occasionally ships *world-readable* by
default — the kernel doesn't lock it down because the bytes are
"not secret in theory", but they reveal the device's serial,
warranty cookie, or rare a one-time-programmed key.

Reads :
  /sys/bus/nvmem/devices/<id>/{type,nvmem,force_ro,...}
    type      : human-friendly provider label
                ("EEPROM", "Battery backed", "OTP", "Unknown")
    nvmem     : the binary data file ; permission bits checked
    force_ro  : 1 = read-only, 0 = writable.

Verdicts (priority order) :
  writable_nvmem               nvmem data file is world-writable
                                 (mode & 0o002) — disaster waiting
                                 to happen.
  world_readable_secret_nvmem  data file is world-readable AND
                                 device name/type hints at OTP /
                                 fuse / TPM / BMC.
  stale_or_unknown_provider    ≥1 device reports type = "Unknown"
                                 — driver didn't set its label
                                 (informational ; common on CMOS
                                 RAM nodes).
  requires_root                /sys/bus/nvmem/devices listing
                                 denied.
  ok                           no NVMEM device exhibits the above
                                 patterns.
  unknown                      /sys/bus/nvmem/devices absent
                                 (kernel without NVMEM subsystem).

stdlib only.
"""
from __future__ import annotations

import os
import re
import stat
from typing import List, Optional


NAME = "nvmem_inventory_audit"


_SYS_NVMEM = "/sys/bus/nvmem/devices"

_SECRET_PROVIDER_NAMES = re.compile(
    r"(?:otp|fuse|tpm|bmc|secure|key|cert|seed|vault|"
    r"vpd|wlan_calib|wifi_eeprom)",
    re.IGNORECASE)


def _read(path: str) -> Optional[str]:
    try:
        with open(path) as f:
            return f.read().strip()
    except OSError:
        return None


def list_nvmem_devices(sys_path: str = _SYS_NVMEM
                            ) -> List[dict]:
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
        entry: dict = {"id": n,
                          "type": _read(os.path.join(d, "type")),
                          "force_ro": _read(os.path.join(
                              d, "force_ro"))}
        nvmem_path = os.path.join(d, "nvmem")
        if os.path.exists(nvmem_path):
            try:
                st = os.stat(nvmem_path)
                entry["nvmem_size"] = st.st_size
                entry["nvmem_mode"] = stat.S_IMODE(st.st_mode)
            except OSError:
                entry["nvmem_size"] = None
                entry["nvmem_mode"] = None
        else:
            entry["nvmem_size"] = None
            entry["nvmem_mode"] = None
        out.append(entry)
    return out


def _is_secret_provider(entry: dict) -> bool:
    """Decide if the device's identity matches a sensitive
    provider — name pattern OR type field OR a known driver
    name."""
    text = " ".join(filter(None, [entry.get("id", ""),
                                          entry.get("type", "") or ""]))
    return bool(_SECRET_PROVIDER_NAMES.search(text))


def classify(devices: List[dict], present: bool,
              listable: bool) -> dict:
    if not present:
        return {"verdict": "unknown",
                "reason": ("/sys/bus/nvmem/devices absent — "
                          "kernel built without the NVMEM "
                          "subsystem."),
                "recommendation": ""}

    if not listable:
        return {"verdict": "requires_root",
                "reason": ("/sys/bus/nvmem/devices listing denied "
                          "— running as unprivileged user."),
                "recommendation": _recipe_requires_root()}

    # 1) writable_nvmem
    writable = [e for e in devices
                  if e.get("nvmem_mode") is not None
                    and (e["nvmem_mode"] & 0o002)]
    if writable:
        sample = ", ".join(
            f"{e['id']} mode=0o{e['nvmem_mode']:03o}"
                for e in writable[:3])
        return {"verdict": "writable_nvmem",
                "reason": (f"{len(writable)} NVMEM data file(s) "
                          f"are world-writable : {sample}."),
                "recommendation": _recipe_writable()}

    # 2) world_readable_secret_nvmem
    secret_readable = [
        e for e in devices
            if e.get("nvmem_mode") is not None
            and (e["nvmem_mode"] & 0o004)
            and _is_secret_provider(e)]
    if secret_readable:
        sample = ", ".join(
            f"{e['id']} ({e.get('type') or '?'})"
                for e in secret_readable[:3])
        return {"verdict": "world_readable_secret_nvmem",
                "reason": (f"{len(secret_readable)} NVMEM data "
                          f"file(s) match a sensitive-provider "
                          f"pattern AND are world-readable : "
                          f"{sample}."),
                "recommendation": _recipe_secret_readable()}

    # 3) stale_or_unknown_provider
    unknown_type = [e for e in devices
                          if (e.get("type") or "").lower()
                              in ("unknown", "", None)]
    if unknown_type:
        sample = ", ".join(e["id"] for e in unknown_type[:3])
        return {"verdict": "stale_or_unknown_provider",
                "reason": (f"{len(unknown_type)} NVMEM device(s) "
                          f"report type = Unknown : {sample}."),
                "recommendation": _recipe_unknown_provider()}

    return {"verdict": "ok",
            "reason": (f"{len(devices)} NVMEM device(s) ; all "
                      f"types known and permissions sane."),
            "recommendation": ""}


def status(config=None, sys_path: str = _SYS_NVMEM) -> dict:
    present = os.path.isdir(sys_path)
    listable = False
    devices: List[dict] = []
    if present:
        try:
            os.listdir(sys_path)
            listable = True
        except OSError:
            listable = False
        if listable:
            devices = list_nvmem_devices(sys_path)
    verdict = classify(devices, present, listable)
    return {"ok": present,
              "present": present,
              "listable": listable,
              "device_count": len(devices),
              "devices": devices,
              "verdict": verdict}


# ── recovery recipes ────────────────────────────────────────────

def _recipe_writable() -> str:
    return ("# A NVMEM data file is world-writable. Lock it :\n"
            "sudo chmod 600 /sys/bus/nvmem/devices/<id>/nvmem\n"
            "# Then track down which driver set the mode :\n"
            "modinfo $(grep -l <id> /sys/bus/nvmem/devices/*/uevent)\n")


def _recipe_secret_readable() -> str:
    return ("# A sensitive NVMEM (OTP / fuse / TPM / BMC) is\n"
            "# world-readable. Restrict :\n"
            "sudo chmod 400 /sys/bus/nvmem/devices/<id>/nvmem\n"
            "# Consider patching the device-tree / DSDT to set\n"
            "# 'read-only;' + 'protected'.\n"
            "# Confirm permissions :\n"
            "ls -l /sys/bus/nvmem/devices/*/nvmem\n")


def _recipe_unknown_provider() -> str:
    return ("# Type=Unknown is typical for CMOS-RAM (cmos_nvram*)\n"
            "# and harmless. For a board EEPROM, expect 'EEPROM'.\n"
            "# Identify the driver :\n"
            "cat /sys/bus/nvmem/devices/<id>/uevent\n")


def _recipe_requires_root() -> str:
    return ("# /sys/bus/nvmem/devices may be 0750 in some\n"
            "# distros. List with elevated perms :\n"
            "sudo ls /sys/bus/nvmem/devices/\n")
