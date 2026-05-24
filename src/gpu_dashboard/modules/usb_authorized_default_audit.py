"""Module usb_authorized_default_audit — per-hub USB
authorization gate (R&D #87.1).

kernel_module_params_drift_audit (R&D #84.3) tracks the
GLOBAL usbcore.authorized_default knob.  This audit goes
per-hub : ``/sys/bus/usb/devices/usb<N>/authorized_default``
can be flipped on individual root hubs after the kernel
module loaded.  Combined with USBGuard presence/absence it
gives the BadUSB / juice-jacking posture.

Reads :

  /sys/bus/usb/devices/usb<N>/authorized_default            0 / 1
  /sys/bus/usb/devices/usb<N>/interface_authorized_default  0 / 1
  /etc/usbguard/                                            policy dir
  /usr/sbin/usbguard /usr/bin/usbguard                      binary

Verdicts (worst first) :

  usb_default_authorized_no_guard   all root hubs have
                                    authorized_default = 1
                                    AND no USBGuard daemon
                                    / policy on disk —
                                    every plug-and-pray
                                    device gets auto-bound.
  usb_mixed_authorization           some hubs at 1, some
                                    at 0 — partial
                                    hardening, drift.
  ok                                hubs at 0 (or
                                    interface-level gating
                                    on) OR USBGuard
                                    present.
  unknown                           /sys/bus/usb/devices
                                    empty (no USB stack).
"""
from __future__ import annotations

import os
import re
from typing import Optional

DEFAULT_USB_ROOT = "/sys/bus/usb/devices"
_USBGUARD_PATHS = (
    "/etc/usbguard",
    "/usr/sbin/usbguard",
    "/usr/bin/usbguard",
)


def _read_int(path: str) -> Optional[int]:
    try:
        with open(path, "r", encoding="utf-8") as fh:
            return int(fh.read().strip())
    except (OSError, PermissionError, ValueError):
        return None


def list_root_hubs(root: str = DEFAULT_USB_ROOT
                    ) -> list[str]:
    try:
        return sorted(
            n for n in os.listdir(root)
            if re.match(r"^usb\d+$", n))
    except OSError:
        return []


def read_hub(root: str, name: str) -> dict:
    d = os.path.join(root, name)
    return {
        "name": name,
        "authorized_default": _read_int(
            os.path.join(d, "authorized_default")),
        "interface_authorized_default": _read_int(
            os.path.join(d, "interface_authorized_default")),
    }


def detect_usbguard(paths: tuple = _USBGUARD_PATHS) -> bool:
    return any(os.path.exists(p) for p in paths)


def classify(hubs: list[dict],
             usbguard_present: bool) -> dict:
    if not hubs:
        return {"verdict": "unknown",
                "reason": (
                    "/sys/bus/usb/devices has no usb<N> "
                    "root hubs — no USB stack visible.")}

    # Hubs with default-allow at either the device or
    # interface level. A hub is "gated" if EITHER
    # authorized_default = 0 OR
    # interface_authorized_default = 0.
    gated = []
    open_hubs = []
    for h in hubs:
        ad = h.get("authorized_default")
        iad = h.get("interface_authorized_default")
        if ad == 0 or iad == 0:
            gated.append(h)
        else:
            open_hubs.append(h)

    if usbguard_present:
        return {"verdict": "ok",
                "reason": (
                    f"USBGuard present ; "
                    f"{len(hubs)} root hub(s) audited "
                    "(daemon enforces device policy).")}

    if not open_hubs:
        return {"verdict": "ok",
                "reason": (
                    f"All {len(hubs)} root hub(s) gate "
                    "either device-level or interface-"
                    "level authorization.")}

    if gated and open_hubs:
        return {"verdict": "usb_mixed_authorization",
                "reason": (
                    f"{len(gated)} hub(s) hardened, "
                    f"{len(open_hubs)} still auto-"
                    "authorize — partial gating, "
                    "drift."),
                "gated_count": len(gated),
                "open_count": len(open_hubs)}

    # All open + no USBGuard
    return {"verdict": "usb_default_authorized_no_guard",
            "reason": (
                f"All {len(hubs)} root hub(s) auto-"
                "authorize new devices AND no USBGuard "
                "daemon is present — BadUSB / juice-"
                "jacking surface."),
            "hub_count": len(hubs)}


def status(config: Optional[dict] = None,
           root: str = DEFAULT_USB_ROOT) -> dict:
    hub_names = list_root_hubs(root)
    hubs = [read_hub(root, n) for n in hub_names]
    usbguard_present = detect_usbguard()
    verdict = classify(hubs, usbguard_present)
    return {
        "ok": verdict["verdict"] not in ("unknown",),
        "hub_count": len(hubs),
        "usbguard_present": usbguard_present,
        "hubs": hubs,
        "verdict": verdict,
    }
