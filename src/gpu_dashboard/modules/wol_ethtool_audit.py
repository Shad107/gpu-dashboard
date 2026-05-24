"""Module wol_ethtool_audit — NIC wake-on-LAN posture +
link-speed regression check (R&D #86.1).

Two homelab footguns hide in /sys/class/net/<iface>/ :

  * WoL silently armed (power/wakeup = enabled) on a NIC
    whose link is down — the PHY keeps drawing power
    through S3 with no chance of waking on a magic
    packet (the link partner is unplugged).
  * A 1 Gb / 2.5 Gb / 10 Gb NIC running at 100 Mb half
    duplex because a worn patch cable or dirty SFP forced
    a downshift — invisible until next backup-night when
    rsync becomes 8× slower.

Existing input_device_audit covers HID device wakeup ;
this audit owns the NIC side.

Reads per /sys/class/net/<iface>/ :

  operstate            up / down / unknown
  carrier              1 / 0  (physical link)
  duplex               full / half / unknown
  speed                Mb/s (-1 if no link or virtual)
  device/power/wakeup  enabled / disabled / "" (no PM)

Skips lo and virtual ifaces whose speed = -1 (virtio,
bridges, tap/tun, veth).

Verdicts (worst first) :

  wakeup_armed_no_link       WoL = enabled  AND
                             (carrier = 0 OR operstate
                             = down) — PHY powered for
                             nothing.
  speed_downshift            link up, speed < 1000 AND
                             duplex = half — silent
                             downshift on a gigabit-class
                             port.
  wakeup_enabled             WoL = enabled on a healthy
                             link (informational — drains
                             ~0.5 W during S3).
  ok                         no anomalies.
  unknown                    /sys/class/net empty / no
                             physical NICs.
"""
from __future__ import annotations

import os
import re
from typing import Optional

DEFAULT_NET_ROOT = "/sys/class/net"

# Ifaces we skip regardless of state.
_SKIP_RE = re.compile(
    r"^(lo|docker\d*|virbr\d+|veth|vmnet\d+|tap|tun|wg\d+|"
    r"br-[0-9a-f]{10,}|kube-)")


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


def list_interfaces(root: str = DEFAULT_NET_ROOT
                     ) -> list[str]:
    try:
        return sorted(os.listdir(root))
    except OSError:
        return []


def read_iface(root: str, name: str) -> dict:
    d = os.path.join(root, name)
    return {
        "name": name,
        "operstate": _read_text(
            os.path.join(d, "operstate")) or "",
        "carrier": _read_int(os.path.join(d, "carrier")),
        "duplex": _read_text(
            os.path.join(d, "duplex")) or "",
        "speed": _read_int(os.path.join(d, "speed")),
        "wakeup": _read_text(
            os.path.join(d, "device", "power", "wakeup"))
                  or "",
    }


def _is_physical(iface: dict) -> bool:
    """Heuristic: we consider an iface "physical" if its
    name doesn't match a virtual pattern AND it has either a
    valid speed reading (≥ 0) or a non-empty duplex setting.

    Virtio guests typically have speed = -1 and duplex =
    "unknown" — we treat those as physical-looking but skip
    the speed-downshift check for them.
    """
    if _SKIP_RE.match(iface["name"]):
        return False
    return True


def classify(ifaces: list[dict]) -> dict:
    physical = [i for i in ifaces if _is_physical(i)]
    if not physical:
        return {"verdict": "unknown",
                "reason": (
                    "No physical network interfaces visible "
                    "in /sys/class/net.")}

    # 1. err — WoL armed but link is down
    for i in physical:
        if i["wakeup"] != "enabled":
            continue
        if i["carrier"] == 0 or i["operstate"] == "down":
            return {
                "verdict": "wakeup_armed_no_link",
                "reason": (
                    f"{i['name']}: WoL = enabled but "
                    f"carrier={i['carrier']} / "
                    f"operstate={i['operstate']} — PHY "
                    "drawing power through S3 with no "
                    "link partner."),
                "iface": i["name"]}

    # 2. warn — speed downshift on physical NIC
    for i in physical:
        s = i["speed"]
        d = i["duplex"]
        # speed = -1 = no link (virtual) ; skip
        if s is None or s <= 0:
            continue
        # Downshift if half-duplex AND < 1 Gb
        if d == "half" and s < 1000:
            return {
                "verdict": "speed_downshift",
                "reason": (
                    f"{i['name']}: link up at {s} Mb/s "
                    f"{d} duplex — gigabit-class NIC "
                    "running degraded ; check cable / SFP."),
                "iface": i["name"], "speed": s,
                "duplex": d}

    # 3. accent — WoL armed on healthy link
    wol_on = [i for i in physical
              if i["wakeup"] == "enabled"]
    if wol_on:
        return {"verdict": "wakeup_enabled",
                "reason": (
                    f"{len(wol_on)} NIC(s) have WoL = "
                    "enabled (informational — adds ~0.5 W "
                    "during S3)."),
                "wol_count": len(wol_on),
                "ifaces": [i["name"] for i in wol_on]}

    return {"verdict": "ok",
            "reason": (
                f"{len(physical)} physical NIC(s) audited ; "
                "no WoL drain, no speed downshift.")}


def status(config: Optional[dict] = None,
           root: str = DEFAULT_NET_ROOT) -> dict:
    ifaces = [read_iface(root, n)
              for n in list_interfaces(root)]
    verdict = classify(ifaces)
    return {
        "ok": verdict["verdict"] not in (
            "unknown", "wakeup_armed_no_link"),
        "iface_count": len(ifaces),
        "interfaces": ifaces,
        "verdict": verdict,
    }
