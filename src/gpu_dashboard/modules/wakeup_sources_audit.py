"""Module wakeup_sources_audit — kernel wakeup sources (R&D #56.4).

Reads /sys/class/wakeup/wakeup*/ (unprivileged) + /sys/power/
wakeup_count + /proc/uptime + best-effort /sys/kernel/debug/
wakeup_sources (root-only, graceful fallback).

Why this matters on an LLM rig that idles between batch jobs :

* A chatty GPE / xhci_hcd / USB hub wakeup source keeps the box
  out of deep idle. RAPL package-C-state residency stays < 20 %,
  the host wastes 6-12 W at idle, and energy-per-token figures get
  skewed.
* On a laptop / SFF host the same chatter accelerates fan wear and
  battery degradation (already surfaced by R&D #51.1 — this
  catches the *root cause*).

Reads :
  /sys/class/wakeup/wakeup*/{name, active_count, event_count,
                                wakeup_count}
  /sys/power/wakeup_count
  /proc/uptime                          (to compute per-hour rate)
  /sys/kernel/debug/wakeup_sources       (root-only, optional)

Verdicts (priority-ordered) :
  s2idle_wakeup_storm           total events/hour > 360 across all
                                sources (~ 1 event every 10 s).
  gpe_chatter_above_1hz         a GPE-named source with rate
                                > 1 event/s.
  usb_hub_chatty                a USB / xhci source with rate
                                > 1 event/s.
  ok                            wakeup sources quiet, debugfs may
                                still be root-only.
  unknown                       /sys/class/wakeup absent.

stdlib only.
"""
from __future__ import annotations

import os
import re
from typing import List, Optional


NAME = "wakeup_sources_audit"


_SYS_WAKEUP = "/sys/class/wakeup"
_SYS_POWER = "/sys/power"
_PROC_UPTIME = "/proc/uptime"
_DEBUGFS_WAKEUP_SOURCES = "/sys/kernel/debug/wakeup_sources"


_WAKEUP_DIR_RE = re.compile(r"^wakeup(\d+)$")
_GPE_NAME_RE = re.compile(r"(?i)\bgpe\b")
_USB_NAME_RE = re.compile(r"(?i)\b(usb|xhci|ehci|uhci|ohci|hub)\b")


def _read(p: str) -> Optional[str]:
    try:
        with open(p) as f:
            return f.read().strip()
    except PermissionError:
        return "__EACCES__"
    except OSError:
        return None


def _read_int(p: str) -> Optional[int]:
    t = _read(p)
    if t is None or t == "__EACCES__":
        return None
    try:
        return int(t)
    except ValueError:
        return None


def list_wakeup_sources(sys_wakeup: str = _SYS_WAKEUP
                          ) -> List[dict]:
    if not os.path.isdir(sys_wakeup):
        return []
    out: List[dict] = []
    for name in sorted(os.listdir(sys_wakeup)):
        if not _WAKEUP_DIR_RE.match(name):
            continue
        d = os.path.join(sys_wakeup, name)
        out.append({
            "id": name,
            "name": _read(os.path.join(d, "name")),
            "active_count": _read_int(
                os.path.join(d, "active_count")),
            "event_count": _read_int(
                os.path.join(d, "event_count")),
            "wakeup_count": _read_int(
                os.path.join(d, "wakeup_count")),
        })
    return out


def read_uptime_seconds(proc_uptime: str = _PROC_UPTIME
                          ) -> Optional[float]:
    text = _read(proc_uptime)
    if not text:
        return None
    try:
        return float(text.split()[0])
    except (ValueError, IndexError):
        return None


def debugfs_readable(path: str = _DEBUGFS_WAKEUP_SOURCES) -> bool:
    if not os.path.exists(path):
        return False
    try:
        with open(path) as f:
            f.read(1)
        return True
    except (PermissionError, OSError):
        return False


def classify(sources: List[dict], uptime_s: Optional[float],
              debugfs_ok: bool) -> dict:
    if not sources:
        return {"verdict": "unknown",
                "reason": ("/sys/class/wakeup is empty or absent."),
                "recommendation": ""}

    # Convert event counts to per-hour rate when uptime known.
    hours = uptime_s / 3600.0 if uptime_s and uptime_s > 0 else None

    def rate_per_hour(events: Optional[int]) -> Optional[float]:
        if events is None or hours is None or hours == 0:
            return None
        return events / hours

    def rate_per_s(events: Optional[int]) -> Optional[float]:
        if events is None or uptime_s is None or uptime_s == 0:
            return None
        return events / uptime_s

    total_events = sum((s.get("event_count") or 0) for s in sources)
    total_rate_h = rate_per_hour(total_events) if hours else None

    # 1) s2idle_wakeup_storm — > 360/hour across all (1 every 10 s)
    if total_rate_h is not None and total_rate_h > 360:
        return {"verdict": "s2idle_wakeup_storm",
                "reason": (f"Total wakeup events = {total_events} "
                          f"in {hours:.1f} h ({total_rate_h:.0f}/h"
                          f"). Deep idle never holds for long."),
                "recommendation": _recipe_wakeup_storm()}

    # 2) gpe_chatter_above_1hz
    gpe_chatty: List[str] = []
    for s in sources:
        name = s.get("name") or ""
        if _GPE_NAME_RE.search(name):
            r = rate_per_s(s.get("event_count"))
            if r is not None and r > 1.0:
                gpe_chatty.append(f"{name}={r:.1f}/s")
    if gpe_chatty:
        return {"verdict": "gpe_chatter_above_1hz",
                "reason": (f"GPE-named source(s) firing > 1/s : "
                          f"{', '.join(gpe_chatty[:3])}."),
                "recommendation": _recipe_gpe()}

    # 3) usb_hub_chatty
    usb_chatty: List[str] = []
    for s in sources:
        name = s.get("name") or ""
        if _USB_NAME_RE.search(name):
            r = rate_per_s(s.get("event_count"))
            if r is not None and r > 1.0:
                usb_chatty.append(f"{name}={r:.1f}/s")
    if usb_chatty:
        return {"verdict": "usb_hub_chatty",
                "reason": (f"USB / hub source(s) firing > 1/s : "
                          f"{', '.join(usb_chatty[:3])}."),
                "recommendation": _recipe_usb_hub()}

    reason = (f"{len(sources)} wakeup source(s), total "
               f"{total_events} events")
    if hours:
        reason += f" over {hours:.1f} h ({total_rate_h:.0f}/h)"
    if not debugfs_ok:
        reason += " ; debugfs deep view is root-only"
    return {"verdict": "ok", "reason": reason + ".",
              "recommendation": ""}


def status(config=None,
            sys_wakeup: str = _SYS_WAKEUP,
            sys_power: str = _SYS_POWER,
            proc_uptime: str = _PROC_UPTIME,
            debugfs_path: str = _DEBUGFS_WAKEUP_SOURCES) -> dict:
    sources = list_wakeup_sources(sys_wakeup)
    uptime_s = read_uptime_seconds(proc_uptime)
    debugfs_ok = debugfs_readable(debugfs_path)
    wakeup_count = _read_int(os.path.join(sys_power,
                                                "wakeup_count"))
    ok = bool(sources)
    verdict = classify(sources, uptime_s, debugfs_ok)
    # Top 5 by event_count for the UI
    sources_top = sorted(
        sources, key=lambda s: -(s.get("event_count") or 0))[:5]
    return {"ok": ok,
              "source_count": len(sources),
              "top_sources": sources_top,
              "uptime_s": uptime_s,
              "wakeup_count": wakeup_count,
              "debugfs_readable": debugfs_ok,
              "verdict": verdict}


# ── recovery recipes ────────────────────────────────────────────

def _recipe_wakeup_storm() -> str:
    return ("# Find the noisiest sources :\n"
            "grep . /sys/class/wakeup/wakeup*/{name,event_count} | \\\n"
            "  awk -F: '{print $0}' | sort -t/ -k4 -n -r | head\n"
            "# For a more authoritative root view :\n"
            "sudo cat /sys/kernel/debug/wakeup_sources | column -t | head -20\n")


def _recipe_gpe() -> str:
    return ("# GPE chatter usually comes from a misbehaving ACPI\n"
            "# device. Map the GPE :\n"
            "sudo cat /sys/firmware/acpi/interrupts/gpe* | head\n"
            "# Common culprit on laptops : an EC firmware update is\n"
            "# needed (vendor support page).\n")


def _recipe_usb_hub() -> str:
    return ("# Disable wakeup on the chatty USB device :\n"
            "for d in /sys/bus/usb/devices/*/power/wakeup; do\n"
            "  echo \"$d : $(cat $d)\"\n"
            "done\n"
            "echo disabled | sudo tee /sys/bus/usb/devices/<bus>-<port>/power/wakeup\n"
            "# Persist via /etc/udev/rules.d/52-usb-wakeup.rules.\n")
