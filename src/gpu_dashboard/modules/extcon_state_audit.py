"""Module extcon_state_audit — USB-C / DP-alt-mode / dock
mux cable-state audit (R&D #85.2).

USB-C, DisplayPort-alt-mode and laptop docking stations all
report their cable / accessory state through the kernel
extcon (external connector) class.  A stuck "connected"
state with no actual consumer, or two mutually-exclusive
connectors (HDMI + DP-alt on the same mux) both asserted,
explains "monitor not detected" or "USB-C charging stopped
working" without any dmesg trace.

Reads :

  /sys/class/extcon/extcon<N>/name      device label
  /sys/class/extcon/extcon<N>/state     text "USB=0\\nHDMI=1"
                                        per cable line
  /sys/class/extcon/extcon<N>/cable.M/  per-cable subdir
                       name             cable label
                       state            integer 0 / 1

Verdicts (worst first) :

  stuck_extcon_state             a cable reports an
                                 invalid/garbage state
                                 value, or its state file
                                 is unreadable on an
                                 otherwise-present extcon
                                 device.
  multiple_connectors_asserted   ≥2 mutually-exclusive
                                 cables on the same extcon
                                 device are asserted
                                 simultaneously (e.g. HDMI
                                 and DP-alt on a USB-C mux).
  ok                             extcon devices present,
                                 all cable states sane.
  n/a                            /sys/class/extcon empty or
                                 contains no extcon<N>
                                 entries.
  unknown                        /sys/class/extcon absent
                                 (kernel without CONFIG_EXTCON
                                 or no devices).
"""
from __future__ import annotations

import os
import re
from typing import Optional

DEFAULT_EXTCON_ROOT = "/sys/class/extcon"

# Cables that are mutually exclusive on the same mux/port —
# asserting both at once is a stuck state, not a normal
# multi-display dock.
_MUX_EXCLUSIVE_GROUPS = (
    {"HDMI", "DP", "DP-A", "DP-B", "Displayport"},
    {"USB", "USB-HOST"},
)


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


def list_extcon_devices(root: str = DEFAULT_EXTCON_ROOT
                         ) -> list[str]:
    try:
        entries = sorted(os.listdir(root))
    except OSError:
        return []
    return [
        e for e in entries
        if re.match(r"^extcon\d+$", e)]


def _parse_state_text(text: Optional[str]) -> list[dict]:
    """Parses the legacy `state` file format :
      USB=0
      USB-HOST=0
      HDMI=1
    or the per-line `<name>=<int>` style.  Returns list of
    {name, asserted_bool}."""
    out: list[dict] = []
    if text is None:
        return out
    for line in text.splitlines():
        line = line.strip()
        if "=" not in line:
            continue
        name, val = line.split("=", 1)
        name = name.strip()
        val = val.strip()
        try:
            iv = int(val)
        except ValueError:
            # Invalid value — record but mark as bad
            out.append({"name": name, "value": val,
                          "asserted": False,
                          "invalid": True})
            continue
        out.append({"name": name, "value": str(iv),
                      "asserted": iv != 0,
                      "invalid": False})
    return out


def _read_cables_subdir(d: str) -> list[dict]:
    """Newer kernels expose cable.<N>/ subdirs."""
    out: list[dict] = []
    try:
        entries = os.listdir(d)
    except OSError:
        return out
    for name in sorted(entries):
        if not name.startswith("cable."):
            continue
        cdir = os.path.join(d, name)
        if not os.path.isdir(cdir):
            continue
        cable_name = (
            _read_text(os.path.join(cdir, "name")) or name)
        state = _read_int(os.path.join(cdir, "state"))
        invalid = (state is None)
        out.append({
            "name": cable_name,
            "value": str(state) if state is not None else "?",
            "asserted": (state is not None and state != 0),
            "invalid": invalid,
        })
    return out


def read_extcon(root: str, name: str) -> dict:
    d = os.path.join(root, name)
    label = (_read_text(os.path.join(d, "name")) or name).strip()
    cables = _read_cables_subdir(d)
    if not cables:
        cables = _parse_state_text(
            _read_text(os.path.join(d, "state")))
    return {
        "node": name,
        "label": label,
        "cables": cables,
    }


def _find_mux_conflict(cables: list[dict]) -> Optional[set]:
    """Returns the set of mutually-exclusive cables both
    asserted, or None if no conflict."""
    asserted = {c["name"].upper() for c in cables
                if c["asserted"]}
    for group in _MUX_EXCLUSIVE_GROUPS:
        inter = asserted & {n.upper() for n in group}
        if len(inter) >= 2:
            return inter
    return None


def classify(devices: list[dict],
             extcon_present: bool) -> dict:
    if not extcon_present:
        return {"verdict": "unknown",
                "reason": (
                    "/sys/class/extcon absent — kernel "
                    "without CONFIG_EXTCON or no extcon "
                    "subsystem.")}
    if not devices:
        return {"verdict": "n/a",
                "reason": (
                    "/sys/class/extcon contains no "
                    "extcon<N> entries — no USB-C / dock "
                    "/ alt-mode hardware exposed.")}

    # 1. err — stuck/invalid cable state
    for dev in devices:
        invalid = [c for c in dev["cables"]
                    if c.get("invalid")]
        if invalid:
            first = invalid[0]
            return {
                "verdict": "stuck_extcon_state",
                "reason": (
                    f"{dev['node']} ({dev['label']}) cable "
                    f"'{first['name']}' has invalid state "
                    f"value '{first['value']}'."),
                "node": dev["node"], "label": dev["label"],
                "cable": first["name"]}

    # 2. accent — multiple mutually-exclusive cables asserted
    for dev in devices:
        conflict = _find_mux_conflict(dev["cables"])
        if conflict:
            return {
                "verdict": "multiple_connectors_asserted",
                "reason": (
                    f"{dev['node']} ({dev['label']}) has "
                    f"mutually-exclusive cables asserted "
                    f"simultaneously: {','.join(sorted(conflict))}."),
                "node": dev["node"], "label": dev["label"],
                "conflict": sorted(conflict)}

    asserted_count = sum(
        sum(1 for c in d["cables"] if c["asserted"])
        for d in devices)
    return {"verdict": "ok",
            "reason": (
                f"{len(devices)} extcon device(s) ; "
                f"{asserted_count} cable(s) asserted, no "
                "mutual-exclusion conflicts.")}


def status(config: Optional[dict] = None,
           root: str = DEFAULT_EXTCON_ROOT) -> dict:
    extcon_present = os.path.isdir(root)
    nodes = list_extcon_devices(root) if extcon_present else []
    devices = [read_extcon(root, n) for n in nodes]
    verdict = classify(devices, extcon_present)
    return {
        "ok": verdict["verdict"] not in (
            "stuck_extcon_state", "unknown"),
        "device_count": len(devices),
        "devices": devices,
        "verdict": verdict,
    }
