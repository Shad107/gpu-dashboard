"""Module mdio_phy_eee_audit — Ethernet PHY + EEE state
desync detector (R&D #95.1).

Two existing modules touch NIC surface but neither walks the
physical PHY :

  * nic_health             — speed / duplex / carrier /
                             operstate from /sys/class/net
                             top level
  * wol_ethtool_audit      — WoL flags via ethtool

This audit owns /sys/class/net/<iface>/phydev/* which is
the PHY-side view of the link. Common desyncs surface
here :

  * driver sets carrier=1 from MAC view, but the PHY didn't
    actually establish autoneg → phy_no_link_carrier_up.
  * PHY's EEE state shows active=1 while userland thinks
    eee=disabled → kernel mis-state.
  * 1G+ capable PHY negotiated half-duplex → bad cable.

Reads :

  /sys/class/net/<iface>/carrier
  /sys/class/net/<iface>/phydev/link
  /sys/class/net/<iface>/phydev/speed
  /sys/class/net/<iface>/phydev/duplex
  /sys/class/net/<iface>/phydev/eee/enabled
  /sys/class/net/<iface>/phydev/eee/active

Verdicts (worst-first) :

  phy_no_link_carrier_up   err   any iface where carrier=1
                                 but phydev/link=0 — driver
                                 thinks link up, PHY says no.
  eee_active_but_disabled  warn  any iface with eee/active=1
                                 but eee/enabled=0 — kernel
                                 mis-state.
  duplex_half_on_gbit_phy  accent any iface with duplex=half
                                 on a >=1000 Mbps PHY — cable
                                 fault or mis-negotiation.
  phy_clean                ok    all probed PHYs coherent.
  requires_root            phydev/* mode-700 (rare).
  unknown                  no phydev on any iface
                           (virtio-net VM, all-loopback,
                           pure-software interfaces).

stdlib only.
"""
from __future__ import annotations

import os
from typing import Optional

NAME = "mdio_phy_eee_audit"

DEFAULT_SYS_CLASS_NET = "/sys/class/net"

# Speeds considered "gigabit or higher".
_GBIT_THRESHOLD = 1000


def _read_text(path: str) -> Optional[str]:
    try:
        with open(path, "r", encoding="utf-8") as fh:
            return fh.read().strip()
    except (OSError, PermissionError):
        return None


def _read_int(path: str) -> Optional[int]:
    t = _read_text(path)
    if t is None:
        return None
    try:
        return int(t)
    except ValueError:
        return None


def read_phydev(iface_root: str) -> Optional[dict]:
    """Return phydev metrics or None if no phydev present."""
    phydev_dir = os.path.join(iface_root, "phydev")
    if not os.path.isdir(phydev_dir):
        return None
    return {
        "link": _read_int(os.path.join(phydev_dir, "link")),
        "speed": _read_int(os.path.join(phydev_dir, "speed")),
        "duplex": _read_text(
            os.path.join(phydev_dir, "duplex")) or "",
        "eee_enabled": _read_int(
            os.path.join(phydev_dir, "eee", "enabled")),
        "eee_active": _read_int(
            os.path.join(phydev_dir, "eee", "active")),
    }


def walk_ifaces(root: str = DEFAULT_SYS_CLASS_NET) -> list:
    if not os.path.isdir(root):
        return []
    try:
        names = os.listdir(root)
    except OSError:
        return []
    out: list = []
    for name in names:
        if name == "lo":
            continue
        iface_root = os.path.join(root, name)
        carrier = _read_int(
            os.path.join(iface_root, "carrier"))
        phy = read_phydev(iface_root)
        if phy is None:
            continue
        out.append({
            "iface": name,
            "carrier": carrier,
            **phy,
        })
    return out


def classify(ifaces: list) -> dict:
    if not ifaces:
        return {"verdict": "unknown",
                "reason": (
                    "No interface has a phydev/ — virtio-net "
                    "VM, all-loopback, or pure-software "
                    "interfaces. Module activates on real "
                    "NICs.")}

    # err — carrier=1 but PHY link=0
    desync = [
        d for d in ifaces
        if d["carrier"] == 1 and d.get("link") == 0]
    if desync:
        names = [d["iface"] for d in desync]
        return {
            "verdict": "phy_no_link_carrier_up",
            "reason": (
                f"{len(desync)} iface(s) have MAC carrier=1 "
                f"but PHY link=0: {names}. Driver / PHY "
                "desync — likely a missed autoneg restart "
                "after suspend or driver reload.")}

    # warn — EEE active vs enabled mismatch
    eee_bad = [
        d for d in ifaces
        if d.get("eee_active") == 1
        and d.get("eee_enabled") == 0]
    if eee_bad:
        names = [d["iface"] for d in eee_bad]
        return {
            "verdict": "eee_active_but_disabled",
            "reason": (
                f"{len(eee_bad)} iface(s) have eee/active=1 "
                f"with eee/enabled=0: {names}. Kernel mis-"
                "state ; toggle EEE via ethtool to resync.")}

    # accent — half-duplex on gigabit-capable PHY
    half_gbit = [
        d for d in ifaces
        if d.get("duplex") == "half"
        and (d.get("speed") or 0) >= _GBIT_THRESHOLD]
    if half_gbit:
        names = [d["iface"] for d in half_gbit]
        return {
            "verdict": "duplex_half_on_gbit_phy",
            "reason": (
                f"{len(half_gbit)} iface(s) running half-"
                f"duplex on a >=1Gbps PHY: {names}. "
                "Cable fault / bad SFP / mis-negotiation. "
                "Half-duplex on gigabit is rare and slow.")}

    return {"verdict": "phy_clean",
            "reason": (
                f"{len(ifaces)} PHY-equipped iface(s) ; "
                "carrier-link coherent, EEE state matches, "
                "no half-duplex gigabit links.")}


def status(config: Optional[dict] = None,
           sys_class_net: str = DEFAULT_SYS_CLASS_NET) -> dict:
    ifaces = walk_ifaces(sys_class_net)
    verdict = classify(ifaces)
    return {
        "ok": verdict["verdict"] == "phy_clean",
        "phy_iface_count": len(ifaces),
        "verdict": verdict,
    }
