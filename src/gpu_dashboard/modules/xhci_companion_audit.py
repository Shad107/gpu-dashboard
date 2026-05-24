"""Module xhci_companion_audit — USB 3 / USB 2 companion-
controller sanity check (R&D #81.1).

Walks /sys/bus/usb/devices/usb*/ to find each USB root hub,
reads its ``version`` (string like ``2.00`` / ``3.10``) and
maps the parent PCI BDF.  Detects the silent USB-3 → USB-2
fallback that tanks NVMe-over-USB and webcam bandwidth on
desktops where the BIOS xHCI handoff has regressed or a
USB 3 cable has worn out.

Healthy desktop pattern :
  USB 3 root hub  paired with a USB 2 companion on the same
                  PCI device  →  both versions report ``3.x``
                  and ``2.x``.

Drift patterns this catches :

  usb3_root_no_companion       USB 3 root hub exists on a
                               PCI device but the matching
                               USB 2 companion is missing —
                               xHCI handoff broke.
  usb3_root_speed_degraded     USB 3 root hub exists but
                               reports speed < 5000  →
                               xHCI loaded but link
                               negotiation failed.
  usb2_only_legacy             no USB 3 root hubs at all —
                               legacy hardware, VM, or
                               BIOS USB 3 disabled.
  ok                           at least one USB 3 root hub
                               paired with USB 2 companion.
  unknown                      /sys/bus/usb/devices missing
                               or empty (no USB stack).
"""
from __future__ import annotations

import os
import re
from typing import Optional

DEFAULT_USB_ROOT = "/sys/bus/usb/devices"

_VERSION_RE = re.compile(r"\s*(\d+)\.(\d+)\s*")


def _read_text(path: str) -> Optional[str]:
    try:
        with open(path, "r", encoding="utf-8") as fh:
            return fh.read().strip()
    except (OSError, PermissionError):
        return None


def _read_int(path: str) -> Optional[int]:
    s = _read_text(path)
    if s is None or s == "":
        return None
    try:
        return int(s)
    except ValueError:
        return None


def _parent_pci_bdf(usb_root: str, hub_name: str) -> Optional[str]:
    """For /sys/bus/usb/devices/usbN, return the PCI BDF
    of the controller it sits on (e.g. 0000:00:14.0)."""
    try:
        real = os.path.realpath(
            os.path.join(usb_root, hub_name))
    except OSError:
        return None
    parent = os.path.dirname(real)
    bdf = os.path.basename(parent)
    # PCI BDFs look like 0000:00:14.0
    if re.match(r"^[0-9a-f]{4}:[0-9a-f]{2}:[0-9a-f]{2}\.[0-9a-f]$",
                bdf):
        return bdf
    return None


def _parse_version_major(text: Optional[str]) -> Optional[int]:
    if text is None:
        return None
    m = _VERSION_RE.match(text)
    if m is None:
        return None
    return int(m.group(1))


def list_root_hubs(root: str = DEFAULT_USB_ROOT) -> list[dict]:
    try:
        entries = sorted(os.listdir(root))
    except OSError:
        return []
    out: list[dict] = []
    for name in entries:
        # Root hubs are usbN ; child devices have a hyphen.
        if not re.match(r"^usb\d+$", name):
            continue
        d = os.path.join(root, name)
        version = _read_text(os.path.join(d, "version"))
        out.append({
            "node": name,
            "version": version,
            "version_major": _parse_version_major(version),
            "speed": _read_int(os.path.join(d, "speed")),
            "maxchild": _read_int(os.path.join(d, "maxchild")),
            "pci_bdf": _parent_pci_bdf(root, name),
        })
    return out


def classify(hubs: list[dict]) -> dict:
    if not hubs:
        return {"verdict": "unknown",
                "reason": "/sys/bus/usb/devices missing or "
                          "empty — no USB stack."}

    usb3_hubs = [h for h in hubs
                  if h["version_major"] is not None
                  and h["version_major"] >= 3]
    usb2_hubs = [h for h in hubs
                  if h["version_major"] == 2]

    # 1. err — USB 3 root without USB 2 companion on same BDF
    if usb3_hubs:
        usb2_bdfs = {h["pci_bdf"] for h in usb2_hubs
                       if h["pci_bdf"] is not None}
        for h in usb3_hubs:
            if h["pci_bdf"] is None:
                continue
            if h["pci_bdf"] not in usb2_bdfs:
                return {
                    "verdict": "usb3_root_no_companion",
                    "reason": (
                        f"USB 3 root {h['node']} on PCI "
                        f"{h['pci_bdf']} has no USB 2 "
                        "companion — xHCI handoff failed."),
                    "node": h["node"], "bdf": h["pci_bdf"]}

    # 2. warn — USB 3 root with speed < 5000 (no SuperSpeed)
    for h in usb3_hubs:
        if h["speed"] is not None and h["speed"] < 5000:
            return {
                "verdict": "usb3_root_speed_degraded",
                "reason": (
                    f"USB 3 root {h['node']} version "
                    f"{h['version']} but speed = "
                    f"{h['speed']} Mb/s (expected ≥5000) "
                    "— SuperSpeed link not negotiated."),
                "node": h["node"], "speed": h["speed"]}

    # 3. accent — no USB 3 at all
    if not usb3_hubs:
        return {"verdict": "usb2_only_legacy",
                "reason": (
                    f"{len(hubs)} root hub(s), none USB 3 — "
                    "legacy hardware, VM, or USB 3 disabled "
                    "in BIOS."),
                "hub_count": len(hubs)}

    return {"verdict": "ok",
            "reason": (
                f"{len(usb3_hubs)} USB 3 root hub(s) paired "
                f"with USB 2 companions ; "
                f"{len(usb2_hubs)} USB 2 root(s) total.")}


def status(config: Optional[dict] = None,
           usb_root: str = DEFAULT_USB_ROOT) -> dict:
    hubs = list_root_hubs(usb_root)
    verdict = classify(hubs)
    return {
        "ok": verdict["verdict"] not in (
            "unknown", "usb3_root_no_companion"),
        "hub_count": len(hubs),
        "usb3_count": sum(
            1 for h in hubs
            if h["version_major"] is not None
            and h["version_major"] >= 3),
        "usb2_count": sum(
            1 for h in hubs if h["version_major"] == 2),
        "hubs": hubs,
        "verdict": verdict,
    }
