"""Module wmi_bus_audit — per-instance WMI bus audit
(R&D #76.1).

Existing wmi_vendor_audit reads /sys/class/wmi/* (the older
class layout). This audit walks the newer per-instance bus at
/sys/devices/virtual/wmi_bus/wmi_bus-*/{<guid>}/ which exposes
finer-grained metadata :

  instance_count   number of instances of this WMI method
  expensive        1 = method runs expensive ACPI code; user
                     should call only when needed
  object_id        canonical WMI object id (4-char vendor code)
  setable          1 = method has a setter
  driver           symlink to /sys/bus/wmi/drivers/<name>
  modalias         alias used by the kernel for module-binding

Why on a homelab :

* Vendor laptop/desktop WMI methods (Dell, HP, ASUS, Lenovo)
  bind GPU fan/profile/charge-limit controls. An "expensive"
  method that's unbound (no driver symlink) or whose driver
  disappeared after a kernel upgrade silently breaks
  firmware-attributes, platform_profile, and fan-curve hooks
  that the existing dashboard relies on for the RTX 3090.

Verdicts (priority order) :
  expensive_unbound      ≥1 expensive=1 GUID without a
                           bound driver (the costly method
                           sits inert).
  orphan_guid            ≥1 GUID lacks `object_id` (vendor
                           never registered a 4-char id).
  missing_modalias       ≥1 GUID without modalias — kernel
                           module-binding broken.
  stale_binding          ≥1 GUID's `driver` symlink is dangling
                           (points to a missing module).
  ok                     all GUIDs sane.
  unknown                /sys/devices/virtual/wmi_bus absent
                           (KVM, AMI BIOS without WMI).

stdlib only.
"""
from __future__ import annotations

import os
import re
from typing import List, Optional


NAME = "wmi_bus_audit"


_SYS_WMI_BUS = "/sys/devices/virtual/wmi_bus"


_BUS_DIR_RE = re.compile(r"^wmi_bus-")
# WMI GUID directory names look like:
#   8D9DDCBC-A997-11DA-B012-B622A1EF5492
_GUID_RE = re.compile(
    r"^[0-9A-Fa-f]{8}-[0-9A-Fa-f]{4}-[0-9A-Fa-f]{4}-"
    r"[0-9A-Fa-f]{4}-[0-9A-Fa-f]{12}$")


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


def list_wmi_guids(sys_path: str = _SYS_WMI_BUS) -> List[dict]:
    if not os.path.isdir(sys_path):
        return []
    out: List[dict] = []
    try:
        buses = sorted(os.listdir(sys_path))
    except OSError:
        return []
    for b in buses:
        if not _BUS_DIR_RE.match(b):
            continue
        bdir = os.path.join(sys_path, b)
        try:
            entries = sorted(os.listdir(bdir))
        except OSError:
            continue
        for e in entries:
            if not _GUID_RE.match(e):
                continue
            d = os.path.join(bdir, e)
            # Resolve the `driver` symlink if present.
            driver_link = os.path.join(d, "driver")
            driver = None
            driver_dangling = False
            if os.path.islink(driver_link):
                try:
                    tgt = os.readlink(driver_link)
                    driver = os.path.basename(tgt)
                except OSError:
                    pass
                if not os.path.exists(driver_link):
                    driver_dangling = True
            elif os.path.exists(driver_link):
                # Sometimes 'driver' is a regular file (rare).
                driver = "(file)"
            out.append({
                "bus": b,
                "guid": e,
                "instance_count": _read_int(
                    os.path.join(d, "instance_count")),
                "expensive": _read_int(
                    os.path.join(d, "expensive")),
                "object_id": _read(os.path.join(d, "object_id")),
                "setable": _read_int(
                    os.path.join(d, "setable")),
                "driver": driver,
                "driver_dangling": driver_dangling,
                "modalias": _read(os.path.join(d, "modalias")),
            })
    return out


def classify(present: bool, guids: List[dict]) -> dict:
    if not present:
        return {"verdict": "unknown",
                "reason": ("/sys/devices/virtual/wmi_bus absent "
                          "— host has no vendor WMI methods "
                          "(KVM, generic AMI BIOS, etc.)."),
                "recommendation": ""}

    if not guids:
        return {"verdict": "unknown",
                "reason": ("/sys/devices/virtual/wmi_bus exists "
                          "but no GUIDs enumerable."),
                "recommendation": ""}

    # 1) expensive_unbound
    expensive_unbound = [g for g in guids
                                 if g.get("expensive") == 1
                                   and not g.get("driver")]
    if expensive_unbound:
        sample = ", ".join(
            g["guid"][:13] + "..." for g in expensive_unbound[:3])
        return {"verdict": "expensive_unbound",
                "reason": (f"{len(expensive_unbound)} expensive "
                          f"WMI method(s) with no bound driver "
                          f": {sample}."),
                "recommendation": _recipe_expensive()}

    # 2) orphan_guid — no object_id
    orphans = [g for g in guids
                  if not g.get("object_id")]
    if orphans:
        sample = ", ".join(
            g["guid"][:13] + "..." for g in orphans[:3])
        return {"verdict": "orphan_guid",
                "reason": (f"{len(orphans)} WMI GUID(s) without "
                          f"object_id : {sample}. Vendor "
                          f"registration incomplete."),
                "recommendation": _recipe_orphan()}

    # 3) missing_modalias
    no_modalias = [g for g in guids
                          if not g.get("modalias")]
    if no_modalias:
        sample = ", ".join(
            g["guid"][:13] + "..." for g in no_modalias[:3])
        return {"verdict": "missing_modalias",
                "reason": (f"{len(no_modalias)} WMI GUID(s) "
                          f"without modalias : {sample}. "
                          f"Kernel module binding broken."),
                "recommendation": _recipe_modalias()}

    # 4) stale_binding
    stale = [g for g in guids
                if g.get("driver_dangling")]
    if stale:
        sample = ", ".join(
            f"{g['guid'][:13]}... -> {g.get('driver')}"
                for g in stale[:3])
        return {"verdict": "stale_binding",
                "reason": (f"{len(stale)} WMI GUID(s) with "
                          f"dangling driver symlink : {sample}."),
                "recommendation": _recipe_stale()}

    return {"verdict": "ok",
            "reason": (f"{len(guids)} WMI GUID(s) ; "
                      f"{sum(1 for g in guids if g.get('driver'))}"
                      f" bound to drivers ; "
                      f"{sum(1 for g in guids if g.get('expensive') == 1)}"
                      f" marked expensive."),
            "recommendation": ""}


def status(config=None, sys_path: str = _SYS_WMI_BUS) -> dict:
    present = os.path.isdir(sys_path)
    guids = list_wmi_guids(sys_path) if present else []
    verdict = classify(present, guids)
    return {"ok": present,
              "present": present,
              "guid_count": len(guids),
              "expensive_count": sum(
                  1 for g in guids if g.get("expensive") == 1),
              "bound_count": sum(
                  1 for g in guids if g.get("driver")),
              "guids": guids[:20],
              "verdict": verdict}


# ── recovery recipes ────────────────────────────────────────────

def _recipe_expensive() -> str:
    return ("# Expensive WMI methods without a driver run no\n"
            "# real work. Find which kernel module should bind :\n"
            "for d in /sys/devices/virtual/wmi_bus/*/*; do\n"
            "  [ -e \"$d/expensive\" ] || continue\n"
            "  exp=$(cat \"$d/expensive\")\n"
            "  drv=$(readlink \"$d/driver\" 2>/dev/null \\\n"
            "        | xargs -r basename)\n"
            "  echo \"$(basename $d) expensive=$exp driver=$drv\"\n"
            "done\n"
            "# Common bindings : dell-smbios, hp-wmi, ideapad-laptop\n"
            "# Verify modalias to find the right module :\n"
            "cat /sys/devices/virtual/wmi_bus/*/*/modalias\n")


def _recipe_orphan() -> str:
    return ("# WMI GUID without object_id = vendor registration\n"
            "# incomplete. Check dmesg for WMI errors :\n"
            "sudo dmesg | grep -iE 'wmi|ACPI.*WMI' | tail\n")


def _recipe_modalias() -> str:
    return ("# WMI GUID without modalias — kernel can't auto-\n"
            "# bind a module. Inspect :\n"
            "for d in /sys/devices/virtual/wmi_bus/*/*; do\n"
            "  echo \"$(basename $d) : modalias=$(cat $d/modalias 2>/dev/null)\"\n"
            "done\n")


def _recipe_stale() -> str:
    return ("# WMI GUID's driver symlink dangles — module was\n"
            "# rmmod'd. Reload :\n"
            "lsmod | grep -iE 'dell|hp_|lenovo|asus|wmi'\n"
            "sudo modprobe <driver_name>\n")
