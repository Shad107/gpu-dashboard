"""Module drm_audit — DRM connector + EDID + modes (R&D #50.1).

Walks /sys/class/drm/ for card<N> + per-connector card<N>-<NAME>/
exposing :
  status        "connected" / "disconnected" / "unknown"
  enabled       "enabled" / "disabled"
  modes         per-line resolution+rate (e.g. "1920x1080 60.00")
  edid          binary EDID blob (1 byte = 1 byte, we just size it)
  dpms          power state (On / Off / Standby / Suspend)

The actionable signal : a connector marked `enabled` but
`disconnected` — userspace asked the GPU to drive it, but no
monitor is plugged in. Wastes a pipe + can cause modeset retries.

Verdicts (priority-ordered) :
  connector_disconnected_active  ≥1 connector enabled + status=
                                 disconnected.
  no_displays                    no card<N>-* connectors are
                                 connected — headless or
                                 nvidia-only VM passthrough.
  ok                             ≥1 connected display, no enabled-
                                 but-disconnected.
  unknown                        /sys/class/drm unreadable.

stdlib only.
"""
from __future__ import annotations

import os
import re
from typing import Optional


NAME = "drm_audit"


_SYS_CLASS_DRM = "/sys/class/drm"


_CONNECTOR_RE = re.compile(r"^card\d+-")


def _read(p: str) -> Optional[str]:
    try:
        with open(p) as f:
            return f.read()
    except OSError:
        return None


def _file_size(p: str) -> int:
    try:
        return os.path.getsize(p)
    except OSError:
        return 0


def list_connectors(sys_drm: str = _SYS_CLASS_DRM) -> list:
    if not os.path.isdir(sys_drm):
        return []
    out: list = []
    try:
        for name in sorted(os.listdir(sys_drm)):
            if not _CONNECTOR_RE.match(name):
                continue
            d = os.path.join(sys_drm, name)
            if not os.path.isdir(d):
                continue
            modes_text = _read(os.path.join(d, "modes")) or ""
            modes = [m.strip() for m in modes_text.splitlines()
                       if m.strip()]
            out.append({
                "name": name,
                "status": (_read(os.path.join(d, "status"))
                              or "").strip() or None,
                "enabled": (_read(os.path.join(d, "enabled"))
                              or "").strip() or None,
                "dpms": (_read(os.path.join(d, "dpms"))
                            or "").strip() or None,
                "modes": modes,
                "mode_count": len(modes),
                "edid_bytes": _file_size(os.path.join(d, "edid")),
            })
    except OSError:
        return []
    return out


def list_cards(sys_drm: str = _SYS_CLASS_DRM) -> list:
    if not os.path.isdir(sys_drm):
        return []
    try:
        return sorted(n for n in os.listdir(sys_drm)
                        if re.match(r"^card\d+$", n))
    except OSError:
        return []


_RECIPE_DISCONNECTED_ACTIVE = (
    "# A DRM connector is enabled (userspace asked the GPU to drive\n"
    "# it) but reports status=disconnected (no monitor plugged in).\n"
    "# Wastes a display pipe + can cause modeset retries.\n"
    "# Investigate which userspace is enabling it :\n"
    "#  - For Xorg : check /etc/X11/xorg.conf.d/*.conf for 'Monitor'\n"
    "#    sections referencing the dead connector.\n"
    "#  - For Wayland / KDE : System Settings → Display, disable.\n"
    "#  - For GNOME : Settings → Displays, disable.\n"
    "# Or force-disable at boot via kernel cmdline :\n"
    "#   video=DP-2:d  (in GRUB_CMDLINE_LINUX_DEFAULT)"
)


def classify(cards: list, connectors: list) -> dict:
    if not cards and not connectors:
        return {"verdict": "unknown",
                "reason": "/sys/class/drm unreadable.",
                "recommendation": ""}
    disconnected_active = [
        c for c in connectors
        if c.get("status") == "disconnected"
        and c.get("enabled") == "enabled"
    ]
    if disconnected_active:
        names = ", ".join(c["name"] for c in disconnected_active)
        return {"verdict": "connector_disconnected_active",
                "reason": (f"{len(disconnected_active)} DRM "
                           f"connector(s) enabled but reporting "
                           f"status=disconnected : {names}."),
                "recommendation": _RECIPE_DISCONNECTED_ACTIVE}
    connected = [c for c in connectors
                   if c.get("status") == "connected"]
    if not connected:
        return {"verdict": "no_displays",
                "reason": (f"{len(cards)} DRM card(s), "
                           f"{len(connectors)} connector(s) — "
                           f"none connected. Headless or VM "
                           f"passthrough without active display."),
                "recommendation": ""}
    return {"verdict": "ok",
            "reason": (f"{len(cards)} DRM card(s), "
                       f"{len(connected)} connected display(s), "
                       f"no enabled-but-disconnected connectors."),
            "recommendation": ""}


def status(cfg=None) -> dict:
    if not os.path.isdir(_SYS_CLASS_DRM):
        return {
            "ok": False,
            "verdict": {"verdict": "unknown",
                         "reason": "/sys/class/drm unreadable.",
                         "recommendation": ""},
            "cards": [], "connectors": [],
        }
    cards = list_cards(_SYS_CLASS_DRM)
    connectors = list_connectors(_SYS_CLASS_DRM)
    verdict = classify(cards, connectors)
    return {
        "ok": True,
        "card_count": len(cards),
        "cards": cards,
        "connector_count": len(connectors),
        "connectors": connectors,
        "verdict": verdict,
    }
