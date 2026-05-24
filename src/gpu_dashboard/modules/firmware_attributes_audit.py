"""Module firmware_attributes_audit — vendor BIOS attributes
audit (R&D #73.3).

On Dell/Lenovo/HP/MSI workstations a dedicated kernel driver
exposes BIOS-level knobs through /sys/class/firmware-
attributes/<vendor>/. Common entries :

  attributes/<name>/current_value
  attributes/<name>/default_value
  attributes/<name>/possible_values
  attributes/<name>/type
  authentication/Admin/{is_enabled, ...}
  pending_reboot

Why on a homelab :

* A ThinkPad / Latitude / OMEN box silently locked into
  `ThermalMode=Quiet` or `IntelligentCooling=Performance=No`
  caps CPU package power, choking inference throughput.
* `pending_reboot=1` means a recent BIOS-attribute change
  hasn't been applied — the user expects different behaviour
  than the running firmware actually delivers.
* `power_limit_unlocked = Disabled` (Dell undervolt-lock,
  similar Lenovo PL2 unlocks) silently caps PL2 below the
  hardware-allowed maximum.

Reads :
  /sys/class/firmware-attributes/<vendor>/{
      pending_reboot,
      attributes/<name>/current_value,
      authentication/Admin/is_enabled,
  }

Verdicts (priority order) :
  pending_reboot_stuck        ≥1 vendor reports
                                pending_reboot = 1.
  thermal_mode_quiet          ≥1 attribute whose name matches
                                /thermal|cool|mode/ has a
                                current_value matching
                                /quiet|silent|battery_saver/.
  power_limit_unlocked_off    ≥1 attribute named
                                ~/power_limit|pl_unlock/
                                current_value matches
                                /disabled|locked/.
  attributes_absent           directory exists but no
                                attributes subdirs.
  requires_root               directory exists but unreadable.
  ok                          attributes sane.
  unknown                     /sys/class/firmware-attributes
                                absent (no vendor driver, KVM
                                guest, etc.).

stdlib only.
"""
from __future__ import annotations

import os
import re
from typing import List, Optional


NAME = "firmware_attributes_audit"


_SYS_FW_ATTR = "/sys/class/firmware-attributes"


_THERMAL_NAME_RE = re.compile(r"thermal|cool|mode",
                                       re.IGNORECASE)
_THERMAL_VALUE_RE = re.compile(
    r"quiet|silent|battery_saver|saver",
    re.IGNORECASE)
_POWER_NAME_RE = re.compile(
    r"power[_\s]?limit|powerlimit|pl[_\s]?unlock|"
    r"undervolt|unleash",
    re.IGNORECASE)
_POWER_LOCKED_RE = re.compile(r"disabled|locked|off",
                                       re.IGNORECASE)


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


def list_vendors(sys_path: str = _SYS_FW_ATTR) -> List[str]:
    if not os.path.isdir(sys_path):
        return []
    try:
        names = sorted(os.listdir(sys_path))
    except OSError:
        return []
    return [n for n in names
                if os.path.isdir(os.path.join(sys_path, n))]


def list_attributes(sys_path: str, vendor: str) -> List[dict]:
    attr_dir = os.path.join(sys_path, vendor, "attributes")
    if not os.path.isdir(attr_dir):
        return []
    try:
        names = sorted(os.listdir(attr_dir))
    except OSError:
        return []
    out: List[dict] = []
    for n in names:
        d = os.path.join(attr_dir, n)
        if not os.path.isdir(d):
            continue
        out.append({
            "vendor": vendor,
            "name": n,
            "current_value": _read(
                os.path.join(d, "current_value")),
            "default_value": _read(
                os.path.join(d, "default_value")),
            "type": _read(os.path.join(d, "type")),
        })
    return out


def read_pending_reboot(sys_path: str,
                            vendor: str) -> Optional[int]:
    return _read_int(os.path.join(
        sys_path, vendor, "pending_reboot"))


def classify(present: bool,
              vendors: List[str],
              attributes: List[dict],
              pending_reboots: List[dict]) -> dict:
    if not present:
        return {"verdict": "unknown",
                "reason": ("/sys/class/firmware-attributes "
                          "absent — no vendor BIOS-attributes "
                          "driver loaded (KVM, AMI / older "
                          "boards)."),
                "recommendation": ""}

    # 1) pending_reboot_stuck
    stuck = [p for p in pending_reboots
                if (p.get("value") or 0) == 1]
    if stuck:
        sample = ", ".join(p["vendor"] for p in stuck[:3])
        return {"verdict": "pending_reboot_stuck",
                "reason": (f"{len(stuck)} vendor(s) report "
                          f"pending_reboot=1 : {sample}. "
                          f"Reboot to apply BIOS changes."),
                "recommendation": _recipe_pending_reboot()}

    # 2) thermal_mode_quiet
    quiet = [a for a in attributes
                if a.get("name")
                and _THERMAL_NAME_RE.search(a["name"])
                and a.get("current_value")
                and _THERMAL_VALUE_RE.search(a["current_value"])]
    if quiet:
        sample = ", ".join(
            f"{a['vendor']}/{a['name']}={a['current_value']}"
                for a in quiet[:3])
        return {"verdict": "thermal_mode_quiet",
                "reason": (f"{len(quiet)} BIOS attribute(s) in "
                          f"quiet / battery-saver mode : "
                          f"{sample}. Inference throughput "
                          f"capped."),
                "recommendation": _recipe_thermal_quiet()}

    # 3) power_limit_unlocked_off
    locked = [a for a in attributes
                  if a.get("name")
                  and _POWER_NAME_RE.search(a["name"])
                  and a.get("current_value")
                  and _POWER_LOCKED_RE.search(a["current_value"])]
    if locked:
        sample = ", ".join(
            f"{a['vendor']}/{a['name']}={a['current_value']}"
                for a in locked[:3])
        return {"verdict": "power_limit_unlocked_off",
                "reason": (f"{len(locked)} power-unlock "
                          f"attribute(s) disabled : {sample}."),
                "recommendation": _recipe_power_locked()}

    # 4) attributes_absent
    if vendors and not attributes:
        return {"verdict": "attributes_absent",
                "reason": (f"{len(vendors)} vendor driver(s) "
                          f"present "
                          f"({', '.join(vendors[:3])}) but no "
                          f"attributes enumerable — driver may "
                          f"be initializing or admin-locked."),
                "recommendation": _recipe_no_attrs()}

    return {"verdict": "ok",
            "reason": (f"{len(vendors)} vendor(s) ; "
                      f"{len(attributes)} attribute(s), no "
                      f"pending reboot, no thermal/power "
                      f"caps."),
            "recommendation": ""}


def status(config=None,
            sys_path: str = _SYS_FW_ATTR) -> dict:
    present = os.path.isdir(sys_path)
    vendors = list_vendors(sys_path)
    attributes: List[dict] = []
    pending: List[dict] = []
    for v in vendors:
        attributes.extend(list_attributes(sys_path, v))
        pending.append({"vendor": v,
                            "value": read_pending_reboot(
                                sys_path, v)})
    verdict = classify(present, vendors, attributes, pending)
    return {"ok": present,
              "present": present,
              "vendor_count": len(vendors),
              "vendors": vendors,
              "attribute_count": len(attributes),
              "attributes_sample": attributes[:12],
              "pending_reboot": pending,
              "verdict": verdict}


# ── recovery recipes ────────────────────────────────────────────

def _recipe_pending_reboot() -> str:
    return ("# A BIOS attribute change is waiting for reboot.\n"
            "# Confirm what's pending :\n"
            "for v in /sys/class/firmware-attributes/*; do\n"
            "  echo \"$v pending=$(cat $v/pending_reboot)\"\n"
            "done\n"
            "# Reboot to apply the change.\n")


def _recipe_thermal_quiet() -> str:
    return ("# Quiet / battery-saver thermal profile is active.\n"
            "# List + change :\n"
            "for a in /sys/class/firmware-attributes/*/\\\n"
            "  attributes/*/current_value; do\n"
            "  echo \"$a = $(cat $a)\"\n"
            "done | grep -iE 'thermal|cool|mode'\n"
            "# Write a higher-performance value (vendor-specific):\n"
            "echo 'Performance' | sudo tee \\\n"
            "  /sys/class/firmware-attributes/<vendor>/\\\n"
            "  attributes/<name>/current_value\n")


def _recipe_power_locked() -> str:
    return ("# Power-limit unlock is disabled. Inspect :\n"
            "for a in /sys/class/firmware-attributes/*/\\\n"
            "  attributes/*/current_value; do\n"
            "  echo \"$a = $(cat $a)\"\n"
            "done | grep -iE 'power|undervolt|unleash'\n"
            "# Vendor-specific : Dell / Lenovo / HP doc.\n")


def _recipe_no_attrs() -> str:
    return ("# Driver loaded but no attributes — usually means\n"
            "# the BIOS admin password is set and attributes are\n"
            "# locked. Unlock via the vendor utility :\n"
            "for v in /sys/class/firmware-attributes/*; do\n"
            "  ls \"$v\"/authentication/ 2>/dev/null\n"
            "done\n")
