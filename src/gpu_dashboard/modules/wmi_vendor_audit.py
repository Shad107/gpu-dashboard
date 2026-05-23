"""Module wmi_vendor_audit — WMI + vendor platform driver (R&D #49.3).

Walks /sys/class/wmi/* + /sys/devices/platform/<vendor-driver>/
for vendor-laptop / vendor-workstation platform features.

Most workstations + laptops expose vendor-specific platform drivers
that toggle dGPU MUX, battery charge thresholds, fan profiles, and
USB-PD over WMI. The actionable signals :

  /sys/class/wmi/*                    GUIDs registered by the
                                      kernel WMI framework. Empty
                                      on non-vendor hosts.
  /sys/devices/platform/<DRIVER>/     per-driver knobs :
    charge_control_start_threshold    when to start charging (% )
    charge_control_end_threshold      stop-charging cap
    fan_mode / fan_curve              vendor fan profile.
    cooling_method                    quiet / standard / performance.
    bbswitch / gpu_mux                discrete-GPU power MUX.

Known vendor drivers : thinkpad_acpi (Lenovo), asus-wmi (ASUS),
dell-smbios (Dell), hp-wmi (HP), msi-ec (MSI), gigabyte-wmi
(Gigabyte AORUS), framework_laptop (Framework 13/16).

Verdicts (priority-ordered) :
  battery_threshold_unset    Vendor driver loaded with battery-
                             threshold attributes BUT default 100 /
                             100 (no charge limit) on a laptop —
                             battery degrades faster.
  vendor_driver_active       Vendor driver loaded ; surface info.
  no_wmi                     /sys/class/wmi empty AND no vendor
                             driver found.
  ok                         WMI present but no vendor driver
                             (clean OEM).
  unknown                    /sys/class/wmi unreadable.

stdlib only.
"""
from __future__ import annotations

import os
from typing import Optional


NAME = "wmi_vendor_audit"


_SYS_CLASS_WMI = "/sys/class/wmi"
_SYS_PLATFORM = "/sys/devices/platform"

VENDOR_DRIVERS = (
    "thinkpad_acpi", "asus-wmi", "dell-smbios", "hp-wmi",
    "msi-ec", "gigabyte-wmi", "framework_laptop",
)


def _read(p: str) -> Optional[str]:
    try:
        with open(p) as f:
            return f.read()
    except OSError:
        return None


def _read_int(p: str) -> Optional[int]:
    t = _read(p)
    if t is None:
        return None
    try:
        return int(t.strip())
    except ValueError:
        return None


def list_wmi_guids(sys_wmi: str = _SYS_CLASS_WMI) -> list:
    if not os.path.isdir(sys_wmi):
        return []
    try:
        return sorted(os.listdir(sys_wmi))
    except OSError:
        return []


def detect_vendor_drivers(sys_platform: str = _SYS_PLATFORM) -> list:
    out: list = []
    if not os.path.isdir(sys_platform):
        return out
    for driver in VENDOR_DRIVERS:
        d = os.path.join(sys_platform, driver)
        if os.path.isdir(d):
            attrs: dict = {"name": driver}
            for attr in ("charge_control_start_threshold",
                          "charge_control_end_threshold",
                          "fan_mode", "cooling_method",
                          "battery_mode"):
                v = _read_int(os.path.join(d, attr))
                if v is not None:
                    attrs[attr] = v
            out.append(attrs)
    return out


_RECIPE_BATTERY = (
    "# Battery charge thresholds at default 100/100 — battery wears\n"
    "# faster than necessary. Set 75/80 % for daily-driver use\n"
    "# (Lenovo / ASUS / Framework drivers expose these knobs) :\n"
    "echo 75 | sudo tee /sys/class/power_supply/BAT0/charge_control_start_threshold\n"
    "echo 80 | sudo tee /sys/class/power_supply/BAT0/charge_control_end_threshold\n"
    "# Persist via udev rule or tlp / battery-charge-limit service."
)


def classify(wmi_guids: list, vendor_drivers: list) -> dict:
    if not wmi_guids and not vendor_drivers:
        return {"verdict": "no_wmi",
                "reason": ("/sys/class/wmi empty + no vendor "
                           "platform driver found. Likely a "
                           "non-OEM workstation, server, or VM."),
                "recommendation": ""}
    # Battery-threshold check : default 100/100 = no limit.
    for vd in vendor_drivers:
        start = vd.get("charge_control_start_threshold")
        end = vd.get("charge_control_end_threshold")
        if (start == 100 and end == 100):
            return {"verdict": "battery_threshold_unset",
                    "reason": (f"{vd['name']} : charge start/end "
                               f"= 100/100 (no limit) — battery "
                               f"wears faster than necessary."),
                    "recommendation": _RECIPE_BATTERY}
    if vendor_drivers:
        names = ", ".join(v["name"] for v in vendor_drivers)
        return {"verdict": "vendor_driver_active",
                "reason": (f"Vendor platform driver(s) loaded : "
                           f"{names}. Surface for visibility — "
                           f"may have battery / fan / GPU-MUX "
                           f"knobs worth exploring."),
                "recommendation": ""}
    return {"verdict": "ok",
            "reason": (f"{len(wmi_guids)} WMI GUID(s) registered, "
                       f"no vendor platform driver loaded."),
            "recommendation": ""}


def status(cfg=None) -> dict:
    if not os.path.isdir(_SYS_CLASS_WMI):
        # WMI framework not present at all — that's fine, fall
        # through to vendor-driver check.
        wmi_guids: list = []
    else:
        wmi_guids = list_wmi_guids(_SYS_CLASS_WMI)
    vendor_drivers = detect_vendor_drivers(_SYS_PLATFORM)
    verdict = classify(wmi_guids, vendor_drivers)
    return {
        "ok": True,
        "wmi_guid_count": len(wmi_guids),
        "wmi_guids": wmi_guids,
        "vendor_drivers": vendor_drivers,
        "verdict": verdict,
    }
