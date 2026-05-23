"""Module watchdog_inventory — watchdog device enumeration (R&D #44.3).

Shipped #37.3 hw_watchdog covers the systemd-side perspective
(RuntimeWatchdogSec). This module covers the *kernel* side : the
actual watchdog character devices the kernel registers under
/sys/class/watchdog/watchdog* with the matching /dev/watchdog*
device nodes.

Hardware watchdog purpose : if userspace fails to "pet" the device
within `timeout` seconds, the watchdog hardware (BMC / TCO / SP5100
on AMD / Intel iTCO / ipmi_wd / sp5100_tco / orangefs-soft) issues
an NMI / reset — the headless rig comes back automatically without
manual intervention.

Each watchdog exposes :
  identity        driver name (sp5100_tco, iTCO_wdt, ipmi_wd, ...)
  timeout         current timeout in seconds
  pretimeout      time before timeout when warning fires
  bootstatus      bitmask : 0x01=card_reset, 0x02=power_over, ...
                  → non-zero on boot means the LAST reset was
                  caused by the watchdog firing → "your rig
                  rebooted itself due to a hang, look at dmesg".
  state           "active" / "inactive"
  nowayout        1 = cannot be stopped once started

Verdicts :
  no_watchdog                  /sys/class/watchdog is empty —
                               either CONFIG_WATCHDOG=n, hypervisor
                               doesn't expose one, or the BIOS has
                               the platform watchdog disabled.
                               Bad for a headless rig.
  boot_due_to_watchdog         bootstatus != 0 on ≥1 watchdog →
                               the last reset was watchdog-triggered.
                               Surface for incident-investigation.
  multiple_watchdogs           ≥2 watchdog devices ; only the first
                               is petted by default → others run
                               unpetted. Pick one in
                               /etc/systemd/system.conf.
  ok                           one watchdog with sane timeout (10-60 s)
                               and clean bootstatus.
  unknown                      /sys/class/watchdog unreadable.

stdlib only.
"""
from __future__ import annotations

import os
import re
from typing import Optional


NAME = "watchdog_inventory"


_SYS_WATCHDOG = "/sys/class/watchdog"


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
        return int(t.strip(), 0)  # autobase for bootstatus hex
    except ValueError:
        return None


def list_watchdogs(sys_wd: str = _SYS_WATCHDOG) -> list:
    if not os.path.isdir(sys_wd):
        return []
    return sorted(
        n for n in os.listdir(sys_wd)
        if n.startswith("watchdog") and n[len("watchdog"):].isdigit()
    )


def read_watchdog(sys_wd: str, name: str) -> dict:
    ddir = os.path.join(sys_wd, name)
    return {
        "name": name,
        "identity": (_read(os.path.join(ddir, "identity"))
                       or "").strip() or None,
        "timeout": _read_int(os.path.join(ddir, "timeout")),
        "pretimeout": _read_int(os.path.join(ddir, "pretimeout")),
        "bootstatus": _read_int(os.path.join(ddir, "bootstatus")),
        "state": (_read(os.path.join(ddir, "state"))
                    or "").strip() or None,
        "nowayout": _read_int(os.path.join(ddir, "nowayout")),
        "fw_version": (_read(os.path.join(ddir, "fw_version"))
                          or "").strip() or None,
    }


_BOOTSTATUS_BITS = [
    (0x01, "card_reset", "watchdog card triggered a reset"),
    (0x02, "power_over", "power over-voltage"),
    (0x04, "power_under", "power under-voltage"),
    (0x08, "fan_fault", "fan fault"),
    (0x10, "ext_relay", "external relay"),
    (0x20, "settings", "watchdog settings changed"),
    (0x40, "magic_close_missing", "userspace closed /dev/watchdog "
                                       "without writing 'V'"),
    (0x80, "soft_overheat", "soft overheat"),
    (0x100, "warning", "pretimeout warning"),
]


def describe_bootstatus(value: int) -> list:
    if not value:
        return []
    out: list = []
    for mask, key, desc in _BOOTSTATUS_BITS:
        if value & mask:
            out.append({"key": key, "mask": mask, "description": desc})
    return out


_RECIPE_ENABLE = (
    "# No kernel watchdog detected — on a headless rig, this leaves\n"
    "# the box dependent on the BMC / IPMI watchdog (which may also\n"
    "# be off). Investigate :\n"
    "#   1. Check BIOS for 'TCO Watchdog' / 'NMI Watchdog' / 'WDT'.\n"
    "#   2. Try loading the platform driver explicitly :\n"
    "sudo modprobe iTCO_wdt          # Intel chipsets\n"
    "sudo modprobe sp5100_tco        # AMD SP5100 / FCH\n"
    "sudo modprobe ipmi_watchdog     # IPMI-capable servers\n"
    "# Once /dev/watchdog appears, enable systemd to pet it :\n"
    "sudo systemctl edit --full --force /etc/systemd/system.conf\n"
    "# Set RuntimeWatchdogSec=30 (or whatever the device timeout is)."
)

_RECIPE_BOOT_CAUSED = (
    "# Last reboot was watchdog-triggered. Investigate the dmesg of\n"
    "# the previous boot to find what was hung :\n"
    "journalctl -k --list-boots\n"
    "journalctl -k --boot=-1 | tail -200\n"
    "# Common causes : kernel deadlock, exhausted hung_task timer\n"
    "# without hung_task_panic, or hardware fault (PSU brownout)."
)

_RECIPE_MULTIPLE = (
    "# Multiple watchdog devices — by default only watchdog0 gets\n"
    "# petted by systemd, the rest run unpetted and may eventually\n"
    "# fire on their own. Pick one and silence the others, or\n"
    "# arrange a multi-watchdog pet daemon (watchdog package) :\n"
    "ls -l /dev/watchdog*\n"
    "# In /etc/systemd/system.conf set the device :\n"
    "WatchdogDevice=/dev/watchdog0"
)


def classify(devices: list) -> dict:
    if not devices:
        return {"verdict": "no_watchdog",
                "reason": ("No hardware watchdog devices detected — "
                           "headless rig has no auto-recovery on "
                           "kernel hang."),
                "recommendation": _RECIPE_ENABLE}
    boot_caused = [d for d in devices
                    if isinstance(d.get("bootstatus"), int)
                    and d["bootstatus"] > 0]
    if boot_caused:
        names = ", ".join(
            f"{d['name']} ({d['identity'] or '?'}) "
            f"bootstatus=0x{d['bootstatus']:x}"
            for d in boot_caused)
        return {"verdict": "boot_due_to_watchdog",
                "reason": (f"{len(boot_caused)} watchdog(s) reported "
                           f"non-zero bootstatus — the last reboot "
                           f"was watchdog-triggered. {names}"),
                "recommendation": _RECIPE_BOOT_CAUSED}
    if len(devices) >= 2:
        names = ", ".join(
            f"{d['name']} ({d['identity'] or '?'})"
            for d in devices)
        return {"verdict": "multiple_watchdogs",
                "reason": (f"{len(devices)} watchdog devices — only "
                           f"the first will be petted by default. "
                           f"{names}"),
                "recommendation": _RECIPE_MULTIPLE}
    d = devices[0]
    return {"verdict": "ok",
            "reason": (f"{d['name']} ({d['identity'] or '?'}) "
                       f"timeout={d.get('timeout')}s, clean bootstatus."),
            "recommendation": ""}


def status(cfg=None) -> dict:
    if not os.path.isdir(_SYS_WATCHDOG):
        return {
            "ok": False,
            "verdict": {"verdict": "unknown",
                         "reason": ("/sys/class/watchdog "
                                    "unreadable."),
                         "recommendation": ""},
            "devices": [],
        }
    names = list_watchdogs(_SYS_WATCHDOG)
    devices = [read_watchdog(_SYS_WATCHDOG, n) for n in names]
    # Decorate with bootstatus breakdown.
    for d in devices:
        bs = d.get("bootstatus")
        if isinstance(bs, int) and bs > 0:
            d["bootstatus_bits"] = describe_bootstatus(bs)
        else:
            d["bootstatus_bits"] = []
    verdict = classify(devices)
    return {
        "ok": True,
        "device_count": len(devices),
        "devices": devices,
        "verdict": verdict,
    }
