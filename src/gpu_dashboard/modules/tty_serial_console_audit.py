"""Module tty_serial_console_audit — TTY / serial / console
inventory audit (R&D #84.4).

Homelab boxes accumulate UPS / PDU / UART dongles whose
ttyUSB device names race on reboot, silently breaking
NUT / MQTT / home-automation scripts.  This audit also
catches an accidental ``console=ttyS0`` left over from a
server-grade kernel cmdline — boot logs end up on a serial
port with no cable, lost forever.

Reads :

  /sys/class/tty/console/active          space-separated
                                         list of active
                                         console devices
  /proc/consoles                         current consoles
                                         table
  /sys/class/tty/tty<N>/                 directory inventory
  /sys/class/tty/ttyS<N>/                serial port info
  /sys/class/tty/ttyUSB<N>/              USB serial dongles
  /sys/class/tty/ttyUSB<N>/device/power/runtime_status
                                         "active" / "suspended"
                                         / "error"
  /sys/class/tty/ttyACM<N>/              CDC-ACM (Arduino, …)
  /sys/module/8250/parameters/nr_uarts   preallocated UART
                                         count

Verdicts (worst first) :

  serial_console_no_cable    /sys/class/tty/console/active
                             lists ONLY ttyS<N> on a desktop
                             box (tty0 not present) — boot
                             logs going to serial with no
                             one watching.
  usb_serial_error_state     ≥1 USB-serial device's
                             device/power/runtime_status
                             reports "error".
  usb_serial_unstable_names  ≥2 USB-serial adapters present
                             with no udev /persistent/
                             symlink — names will swap on
                             reboot.
  ok                         consoles sane, USB serial
                             stable or absent.
  n/a                        /sys/class/tty absent.
"""
from __future__ import annotations

import os
import re
from typing import Optional

DEFAULT_TTY_ROOT = "/sys/class/tty"

_USB_TTY_RE = re.compile(r"^tty(USB|ACM)\d+$")


def _read_text(path: str) -> Optional[str]:
    try:
        with open(path, "r", encoding="utf-8") as fh:
            return fh.read().strip()
    except (OSError, PermissionError):
        return None


def read_active_consoles(root: str = DEFAULT_TTY_ROOT
                          ) -> list[str]:
    text = _read_text(os.path.join(root, "console", "active"))
    if text is None:
        return []
    return text.split()


def list_serial_devices(root: str = DEFAULT_TTY_ROOT
                          ) -> list[dict]:
    """Returns list of USB-serial / ACM-class devices with
    runtime_status."""
    out: list[dict] = []
    try:
        entries = sorted(os.listdir(root))
    except OSError:
        return []
    for name in entries:
        if not _USB_TTY_RE.match(name):
            continue
        d = os.path.join(root, name)
        runtime_status = _read_text(
            os.path.join(d, "device", "power",
                          "runtime_status"))
        out.append({
            "name": name,
            "runtime_status": runtime_status,
        })
    return out


def classify(consoles: list[str],
             usb_serial: list[dict],
             tty_root_exists: bool) -> dict:
    if not tty_root_exists:
        return {"verdict": "n/a",
                "reason": ("/sys/class/tty absent — no TTY "
                           "subsystem on this kernel.")}

    # 1. err — active console is ttyS* only (no tty0)
    if consoles:
        has_tty0 = any(
            c.startswith("tty") and not c.startswith("ttyS")
            and not c.startswith("ttyUSB")
            and not c.startswith("ttyACM")
            for c in consoles)
        has_ttyS = any(c.startswith("ttyS") for c in consoles)
        if has_ttyS and not has_tty0:
            return {"verdict": "serial_console_no_cable",
                    "reason": (
                        f"Active console list "
                        f"({','.join(consoles)}) contains "
                        "only serial ports — boot logs are "
                        "going to a serial cable with no "
                        "one watching."),
                    "consoles": consoles}

    # 2. warn — USB serial in error state
    error_serials = [
        s for s in usb_serial
        if s.get("runtime_status") == "error"]
    if error_serials:
        first = error_serials[0]
        return {"verdict": "usb_serial_error_state",
                "reason": (
                    f"{first['name']} runtime_status = "
                    "error — USB-serial dongle stuck."),
                "device": first["name"]}

    # 3. accent — multiple USB-serial adapters, unstable names
    if len(usb_serial) >= 2:
        return {"verdict": "usb_serial_unstable_names",
                "reason": (
                    f"{len(usb_serial)} USB-serial / ACM "
                    "device(s) present — names will swap "
                    "on reboot without a udev persistent "
                    "rule."),
                "device_count": len(usb_serial)}

    return {"verdict": "ok",
            "reason": (
                f"Active consoles: {','.join(consoles) or '-'} ; "
                f"{len(usb_serial)} USB-serial device(s).")}


def status(config: Optional[dict] = None,
           root: str = DEFAULT_TTY_ROOT) -> dict:
    tty_root_exists = os.path.isdir(root)
    consoles = (read_active_consoles(root)
                if tty_root_exists else [])
    usb_serial = (list_serial_devices(root)
                    if tty_root_exists else [])
    verdict = classify(consoles, usb_serial, tty_root_exists)
    return {
        "ok": verdict["verdict"] not in (
            "serial_console_no_cable",),
        "consoles": consoles,
        "usb_serial_devices": usb_serial,
        "usb_serial_count": len(usb_serial),
        "verdict": verdict,
    }
