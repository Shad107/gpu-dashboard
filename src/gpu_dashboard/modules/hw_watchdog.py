"""Module hw_watchdog — hardware watchdog auditor (R&D #37.3).

Hardware watchdogs let the kernel detect "this machine has hung" and
trigger a reboot — invaluable for an unattended LLM inference rig
(e.g., the box in the closet serving ollama to your house). Linux
exposes them via /sys/class/watchdog/watchdog<n>/:

  identity     human name (i6300ESB, iTCO_wdt, softdog, …)
  timeout      configured timeout in seconds (0 = unconfigured)
  bootstatus   bitmask — non-zero means the LAST reboot was
               triggered by the watchdog (your inference rig
               hung 12 hours ago and nobody saw it)
  nowayout     1 = closing /dev/watchdog won't disable it
                   (kernel will still reboot on miss)
  state        "active" / "inactive"
  pretimeout   optional warning before reset
  status       hex bitmask of current device status

This module enumerates all watchdog devices and emits:

  no_watchdog    no /sys/class/watchdog entries — VM (host owns
                 watchdog) or kernel without a watchdog driver
  unpinged       device present but timeout=0 — configured but
                 not actually being kicked
  bootstatus_set previous boot was triggered by watchdog — surface
                 to the user as "something hung last time"
  active         device with timeout > 0 — assume a userspace
                 pinger is running ; recipe documents wd_keepalive
                 + systemd RuntimeWatchdogSec= for confirmation
  unknown        sysfs unreadable

Doesn't try to verify a pinger is running (would need to scan
/proc/*/fd for /dev/watchdog) — that's out of XS scope. The recipe
shows what to install if missing.

stdlib only.
"""
from __future__ import annotations

import os
import re
from typing import Optional


NAME = "hw_watchdog"


_WATCHDOG_ROOT = "/sys/class/watchdog"


_WATCHDOG_RE = re.compile(r"^watchdog(\d+)$")


def _read(p: str) -> Optional[str]:
    try:
        with open(p) as f:
            return f.read().strip()
    except OSError:
        return None


def _read_int(p: str) -> Optional[int]:
    s = _read(p)
    if s is None:
        return None
    # bootstatus + status can be hex (0x8000)
    try:
        if s.startswith(("0x", "0X")):
            return int(s, 16)
        return int(s)
    except ValueError:
        return None


def list_watchdogs(root: str = _WATCHDOG_ROOT) -> list:
    try:
        names = sorted(os.listdir(root))
    except OSError:
        return []
    pairs: list = []
    for n in names:
        m = _WATCHDOG_RE.match(n)
        if m and os.path.isdir(os.path.join(root, n)):
            pairs.append((int(m.group(1)), n))
    pairs.sort()
    return [p[1] for p in pairs]


def read_watchdog(root: str, name: str) -> dict:
    base = os.path.join(root, name)
    return {
        "watchdog": name,
        "identity": _read(os.path.join(base, "identity")) or "",
        "timeout": _read_int(os.path.join(base, "timeout")),
        "bootstatus": _read_int(os.path.join(base, "bootstatus")),
        "nowayout": _read_int(os.path.join(base, "nowayout")),
        "state": _read(os.path.join(base, "state")),
        "pretimeout": _read_int(os.path.join(base, "pretimeout")),
        "status": _read(os.path.join(base, "status")),
    }


_RECIPE_PINGER = (
    "# Confirm a userspace pinger is running:\n"
    "lsof /dev/watchdog0 2>/dev/null    # any process holding it?\n"
    "# If nothing's pinging, install + enable wd_keepalive (Debian):\n"
    "sudo apt install watchdog\n"
    "sudo systemctl enable --now wd_keepalive\n"
    "# OR use systemd-native:\n"
    "# Edit /etc/systemd/system.conf, set:\n"
    "#   RuntimeWatchdogSec=20\n"
    "#   ShutdownWatchdogSec=2min\n"
    "# Then `sudo systemctl daemon-reexec`.\n"
)

_RECIPE_BOOTSTATUS = (
    "# Non-zero bootstatus = previous boot was triggered by the\n"
    "# watchdog ; the system hung and the chip reset it. Pull the\n"
    "# pre-reboot dmesg to figure out what froze:\n"
    "sudo journalctl -k -b -1 --no-pager | tail -100\n"
    "# Also see #28.7 nvrm_tail (driver log) and #34.3 oomd_correlator."
)


def classify(watchdogs: list) -> dict:
    if not watchdogs:
        return {"verdict": "no_watchdog",
                "reason": ("No /sys/class/watchdog/watchdog* entries — "
                           "VM (host owns the watchdog), kernel without "
                           "a watchdog driver, or hardware doesn't "
                           "expose one. No unattended-reboot fallback "
                           "for the inference rig."),
                "recommendation": ""}
    # bootstatus_set takes priority — last boot was triggered by a hang
    for w in watchdogs:
        if w.get("bootstatus") and w["bootstatus"] > 0:
            return {"verdict": "bootstatus_set",
                    "reason": (f"watchdog `{w['identity']}` "
                               f"({w['watchdog']}) reports "
                               f"bootstatus={w['bootstatus']} — the "
                               f"previous boot was triggered by the "
                               f"watchdog. Something hung."),
                    "recommendation": _RECIPE_BOOTSTATUS}
    # unpinged: timeout=0 means device exists but isn't configured
    unpinged = [w for w in watchdogs
                  if w.get("timeout") is not None and w["timeout"] == 0]
    if unpinged:
        names = [w["identity"] or w["watchdog"] for w in unpinged]
        return {"verdict": "unpinged",
                "reason": (f"watchdog(s) {names} have timeout=0 — "
                           f"device present but not configured / not "
                           f"being kicked. Effectively inert."),
                "recommendation": _RECIPE_PINGER}
    identities = [w["identity"] or w["watchdog"] for w in watchdogs]
    return {"verdict": "active",
            "reason": (f"{len(watchdogs)} watchdog(s) configured: "
                       f"{', '.join(identities)}. Verify a userspace "
                       f"pinger is running."),
            "recommendation": _RECIPE_PINGER}


def status(cfg=None) -> dict:
    names = list_watchdogs(_WATCHDOG_ROOT)
    watchdogs = [read_watchdog(_WATCHDOG_ROOT, n) for n in names]
    verdict = classify(watchdogs)
    return {
        "ok": True,
        "watchdog_count": len(watchdogs),
        "watchdogs": watchdogs,
        "verdict": verdict,
    }
