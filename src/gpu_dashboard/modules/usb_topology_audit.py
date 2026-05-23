"""Module usb_topology_audit — USB tree power + speed (R&D #48.2).

Walks /sys/bus/usb/devices/* (skipping interface-only paths
shaped `X-Y:Z.W`) and reads per-device {idVendor, idProduct,
manufacturer, product, speed, version, bMaxPower, authorized,
power/control, power/autosuspend_delay_ms} for the right
USB-tree posture.

Verdicts (priority-ordered) :
  power_budget_high       Total bMaxPower draw across non-root-hub
                          devices > 500 mA on a single USB host
                          controller — risk of brownout on bus-
                          powered devices.
  speed_negotiated_low    ≥1 device negotiated USB 1.x or 2.0 LS
                          (1.5 / 12 Mbps) when its descriptor
                          claims USB 3.0+ support — typical of
                          dead Vbus on the SS pair, blue-port to
                          a USB-A cable that's USB-2 only, or
                          worn USB-3 hub.
  autosuspend_unfriendly  ≥1 HID/keyboard/mouse/UPS has
                          power/control=auto + a short
                          autosuspend_delay_ms (< 2000) → the
                          kernel suspends the device every few
                          seconds, can drop keystrokes or hide
                          UPS battery events.
  ok                      no flags raised.
  no_usb_devices          /sys/bus/usb/devices empty.
  unknown                 /sys/bus/usb unreadable.

stdlib only.
"""
from __future__ import annotations

import os
import re
from typing import Optional


NAME = "usb_topology_audit"


_SYS_BUS_USB = "/sys/bus/usb/devices"


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


def _parse_bMaxPower(text: Optional[str]) -> Optional[int]:
    """Format : '500mA\n' — strip 'mA' suffix."""
    if not text:
        return None
    s = text.strip()
    if s.endswith("mA"):
        s = s[:-2]
    try:
        return int(s.strip())
    except ValueError:
        return None


_INTERFACE_RE = re.compile(r"^\d+-\d+(\.\d+)*:\d+\.\d+$")


def is_interface_path(name: str) -> bool:
    """Interface paths look like '1-1:1.0' — skip in device walk."""
    return bool(_INTERFACE_RE.match(name))


def is_root_hub(name: str) -> bool:
    """Root hubs are 'usbN'."""
    return name.startswith("usb") and name[3:].isdigit()


def list_devices(sys_usb: str = _SYS_BUS_USB) -> list:
    if not os.path.isdir(sys_usb):
        return []
    out: list = []
    try:
        names = sorted(os.listdir(sys_usb))
    except OSError:
        return []
    for name in names:
        if is_interface_path(name):
            continue
        d = os.path.join(sys_usb, name)
        if not os.path.isdir(d):
            continue
        rec = {"name": name, "is_root_hub": is_root_hub(name)}
        rec["idVendor"] = (_read(os.path.join(d, "idVendor"))
                              or "").strip() or None
        rec["idProduct"] = (_read(os.path.join(d, "idProduct"))
                               or "").strip() or None
        rec["manufacturer"] = (_read(os.path.join(d, "manufacturer"))
                                   or "").strip() or None
        rec["product"] = (_read(os.path.join(d, "product"))
                              or "").strip() or None
        rec["speed_mbps"] = _read_int(os.path.join(d, "speed"))
        rec["version"] = (_read(os.path.join(d, "version"))
                              or "").strip() or None
        rec["bMaxPower_mA"] = _parse_bMaxPower(
            _read(os.path.join(d, "bMaxPower")))
        rec["authorized"] = _read_int(os.path.join(d, "authorized"))
        rec["power_control"] = (_read(os.path.join(
            d, "power", "control")) or "").strip() or None
        rec["autosuspend_delay_ms"] = _read_int(
            os.path.join(d, "power", "autosuspend_delay_ms"))
        rec["bcdDevice"] = (_read(os.path.join(d, "bcdDevice"))
                                or "").strip() or None
        out.append(rec)
    return out


_HID_KEYWORDS = ("keyboard", "mouse", "hid", "ups", "trackpad",
                  "battery", "yubikey")


def _looks_hid_like(d: dict) -> bool:
    haystack = " ".join(filter(None, [d.get("product"),
                                          d.get("manufacturer")])).lower()
    return any(k in haystack for k in _HID_KEYWORDS)


_AUTOSUSPEND_MIN_HID_MS = 2000
_POWER_BUDGET_THRESHOLD_MA = 500
_SS_VERSION_STRINGS = ("3.00", "3.10", "3.20", "3.0", "3.1", "3.2")
_SS_MIN_MBPS = 5000


_RECIPE_POWER_BUDGET = (
    "# Total bMaxPower across non-root-hub USB devices exceeds\n"
    "# 500 mA on a single host controller — risk of brownout on\n"
    "# bus-powered devices (external SSDs, charging dongles).\n"
    "# Move some devices to a powered hub or to a different\n"
    "# controller (different `usbN` root)."
)

_RECIPE_SPEED_LOW = (
    "# A USB 3.0+-capable device negotiated USB 1.x/2.0 speed.\n"
    "# Common causes : USB-A cable that's USB-2 only (no SS pair),\n"
    "# worn SS connector on the host port, or hub downgrade.\n"
    "# Verify via :\n"
    "lsusb -t\n"
    "# Then try a different physical port / known-good cable."
)

_RECIPE_AUTOSUSPEND = (
    "# A HID/UPS device has power/control=auto + autosuspend_delay\n"
    "# < 2000 ms — kernel suspends it every few seconds, can drop\n"
    "# keystrokes or hide UPS battery events. Disable autosuspend\n"
    "# for that device :\n"
    "echo on | sudo tee /sys/bus/usb/devices/<NAME>/power/control\n"
    "# Persistent via udev rule :\n"
    "#   ATTR{idVendor}==\"<VID>\", ATTR{idProduct}==\"<PID>\",\n"
    "#     TEST==\"power/control\", ATTR{power/control}=\"on\""
)


def classify(devices: list) -> dict:
    if not devices:
        return {"verdict": "no_usb_devices",
                "reason": "No USB devices in /sys/bus/usb/devices.",
                "recommendation": ""}
    # 1) Power budget per root-hub group : sum bMaxPower of
    #    non-root-hub devices.
    non_root = [d for d in devices if not d.get("is_root_hub")]
    total_ma = sum(d.get("bMaxPower_mA") or 0 for d in non_root)
    if total_ma > _POWER_BUDGET_THRESHOLD_MA:
        return {"verdict": "power_budget_high",
                "reason": (f"Total bMaxPower draw across "
                           f"{len(non_root)} non-root-hub device(s) "
                           f"is {total_ma} mA — exceeds "
                           f"{_POWER_BUDGET_THRESHOLD_MA} mA "
                           f"recommended ceiling."),
                "recommendation": _RECIPE_POWER_BUDGET}
    # 2) Speed-negotiation downgrade : version claims 3.0+ but
    #    speed < 5000 Mbps.
    downgraded: list = []
    for d in non_root:
        ver = (d.get("version") or "").strip()
        speed = d.get("speed_mbps") or 0
        ver_short = ver.lstrip().lstrip("0") or ver
        is_ss_capable = any(s in ver for s in _SS_VERSION_STRINGS)
        if is_ss_capable and speed and speed < _SS_MIN_MBPS:
            downgraded.append((d, speed))
    if downgraded:
        names = ", ".join(
            f"{d['name']} ({d.get('product') or '?'}) speed={s}Mbps"
            for d, s in downgraded[:3])
        return {"verdict": "speed_negotiated_low",
                "reason": (f"{len(downgraded)} USB 3.0+-capable "
                           f"device(s) negotiated < 5 Gbps : "
                           f"{names}"),
                "recommendation": _RECIPE_SPEED_LOW}
    # 3) HID autosuspend foot-gun.
    bad_susp: list = []
    for d in non_root:
        if (_looks_hid_like(d)
                and d.get("power_control") == "auto"
                and (d.get("autosuspend_delay_ms") or 0)
                    < _AUTOSUSPEND_MIN_HID_MS):
            bad_susp.append(d)
    if bad_susp:
        names = ", ".join(d.get("product") or d.get("name")
                            for d in bad_susp[:3])
        return {"verdict": "autosuspend_unfriendly",
                "reason": (f"{len(bad_susp)} HID/UPS-like device(s) "
                           f"with autosuspend < 2 s : {names}"),
                "recommendation": _RECIPE_AUTOSUSPEND}
    return {"verdict": "ok",
            "reason": (f"{len(devices)} USB device(s) "
                       f"({len(non_root)} non-root-hub), total draw "
                       f"{total_ma} mA, no speed downgrades, no "
                       f"hostile autosuspend."),
            "recommendation": ""}


def status(cfg=None) -> dict:
    if not os.path.isdir(_SYS_BUS_USB):
        return {
            "ok": False,
            "verdict": {"verdict": "unknown",
                         "reason": "/sys/bus/usb/devices unreadable.",
                         "recommendation": ""},
            "devices": [],
        }
    devices = list_devices(_SYS_BUS_USB)
    verdict = classify(devices)
    return {
        "ok": True,
        "device_count": len(devices),
        "non_root_count": sum(1 for d in devices
                                  if not d.get("is_root_hub")),
        "total_power_ma": sum(d.get("bMaxPower_mA") or 0
                                for d in devices
                                if not d.get("is_root_hub")),
        "devices": devices,
        "verdict": verdict,
    }
