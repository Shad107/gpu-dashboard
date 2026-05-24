"""Module net_iface_counters_audit — per-interface NIC error
counters audit (R&D #78.1).

Per-NIC statistics under /sys/class/net/<iface>/statistics/
expose drift signals that catch real hardware problems
before user-visible failure on a homelab :

  rx_crc_errors         physical-layer CRC fails — usually a
                          bad SATA-grade RJ45 cable on a 2.5GbE
                          link.
  rx_dropped            kernel ran out of socket buffer.
  tx_errors             upstream rejection.
  carrier_changes       link bounce.
  rx_frame_errors       framing error (cable wrong wiring).
  rx_over_errors        FIFO overrun (interrupt latency).
  collisions            half-duplex collision.
  tx_aborted_errors     send aborted (carrier-loss mid-transmit).

Thresholds favour the desktop / homelab profile :
* `rx_crc_errors > 0` = any CRC = bad cable. No deadband.
* `carrier_changes > 100` per boot = flapping link.
* `rx_dropped / rx_packets > 5 %` = elevated drop ratio.

Reads :
  /sys/class/net/<iface>/statistics/<counter>
  /sys/class/net/<iface>/carrier_changes
  /sys/class/net/<iface>/operstate
  /sys/class/net/<iface>/type                 1 = Ethernet

Skips loopback (`lo`) and tunnels (type != 1).

Verdicts (priority order) :
  rx_crc_storm           ≥1 iface rx_crc_errors > 0.
  tx_errors_climbing     ≥1 iface tx_errors > 0.
  carrier_flapping       ≥1 iface carrier_changes > 100.
  rx_dropped_elevated    ≥1 iface drop ratio > 5 % (skipped
                           when rx_packets < 1000 to avoid
                           false positives on cold ifaces).
  ok                     all counters clean.
  unknown                /sys/class/net absent.

stdlib only.
"""
from __future__ import annotations

import os
from typing import Dict, List, Optional


NAME = "net_iface_counters_audit"


_SYS_NET = "/sys/class/net"


_COUNTERS = (
    "rx_errors", "tx_errors", "rx_dropped", "tx_dropped",
    "collisions", "rx_crc_errors", "rx_frame_errors",
    "rx_over_errors", "tx_aborted_errors",
    "rx_packets", "tx_packets",
)


_CARRIER_FLAP_THRESHOLD = 100
_DROP_RATIO_THRESHOLD = 0.05
_DROP_PACKET_FLOOR = 1000      # don't ratio-judge cold ifaces


def _read(path: str) -> Optional[str]:
    try:
        with open(path) as f:
            return f.read().strip()
    except OSError:
        return None


def _read_int(path: str) -> Optional[int]:
    t = _read(path)
    if t is None:
        return None
    try:
        return int(t)
    except ValueError:
        return None


def list_interfaces(sys_net: str = _SYS_NET) -> List[str]:
    if not os.path.isdir(sys_net):
        return []
    try:
        return sorted(os.listdir(sys_net))
    except OSError:
        return []


def read_iface_stats(sys_net: str, iface: str) -> dict:
    d = os.path.join(sys_net, iface, "statistics")
    out: Dict[str, Optional[int]] = {}
    for k in _COUNTERS:
        out[k] = _read_int(os.path.join(d, k))
    out["carrier_changes"] = _read_int(
        os.path.join(sys_net, iface, "carrier_changes"))
    out["operstate"] = _read(
        os.path.join(sys_net, iface, "operstate"))
    out["type"] = _read_int(
        os.path.join(sys_net, iface, "type"))
    return out


def is_ethernet_or_wifi(stats: dict) -> bool:
    """Return True for Ethernet (type=1) or wireless. Skip
    loopback (type=772) and tunnels (type != 1)."""
    return stats.get("type") == 1


def classify(present: bool,
              ifaces: Dict[str, dict]) -> dict:
    if not present:
        return {"verdict": "unknown",
                "reason": "/sys/class/net absent.",
                "recommendation": ""}

    # Skip lo + non-ethernet
    audited = {n: s for n, s in ifaces.items()
                  if n != "lo" and is_ethernet_or_wifi(s)}

    if not audited:
        return {"verdict": "ok",
                "reason": ("/sys/class/net only contains "
                          "loopback / tunnels — nothing to "
                          "audit."),
                "recommendation": ""}

    # 1) rx_crc_storm
    crc = [n for n, s in audited.items()
              if (s.get("rx_crc_errors") or 0) > 0]
    if crc:
        sample = ", ".join(
            f"{n}={audited[n]['rx_crc_errors']}"
                for n in crc[:3])
        return {"verdict": "rx_crc_storm",
                "reason": (f"{len(crc)} iface(s) report "
                          f"rx_crc_errors > 0 : {sample}. "
                          f"Replace cable / check cabinet "
                          f"shielding."),
                "recommendation": _recipe_crc()}

    # 2) tx_errors_climbing
    tx_err = [n for n, s in audited.items()
                  if (s.get("tx_errors") or 0) > 0]
    if tx_err:
        sample = ", ".join(
            f"{n}={audited[n]['tx_errors']}"
                for n in tx_err[:3])
        return {"verdict": "tx_errors_climbing",
                "reason": (f"{len(tx_err)} iface(s) report "
                          f"tx_errors > 0 : {sample}. "
                          f"Upstream rejection / collision "
                          f"on half-duplex."),
                "recommendation": _recipe_tx_errors()}

    # 3) carrier_flapping
    flap = [n for n, s in audited.items()
              if (s.get("carrier_changes") or 0)
                  > _CARRIER_FLAP_THRESHOLD]
    if flap:
        sample = ", ".join(
            f"{n}={audited[n]['carrier_changes']}"
                for n in flap[:3])
        return {"verdict": "carrier_flapping",
                "reason": (f"{len(flap)} iface(s) with "
                          f">{_CARRIER_FLAP_THRESHOLD} "
                          f"carrier_changes : {sample}. Link "
                          f"bouncing."),
                "recommendation": _recipe_flap()}

    # 4) rx_dropped_elevated
    elevated = []
    for n, s in audited.items():
        rxp = s.get("rx_packets") or 0
        rxd = s.get("rx_dropped") or 0
        if rxp >= _DROP_PACKET_FLOOR:
            if rxd / rxp > _DROP_RATIO_THRESHOLD:
                elevated.append((n, rxd, rxp))
    if elevated:
        sample = ", ".join(
            f"{n} drop={rxd}/{rxp} ({100*rxd/rxp:.1f}%)"
                for n, rxd, rxp in elevated[:3])
        return {"verdict": "rx_dropped_elevated",
                "reason": (f"{len(elevated)} iface(s) drop > "
                          f"{100*_DROP_RATIO_THRESHOLD:.0f} % "
                          f"of rx packets : {sample}."),
                "recommendation": _recipe_dropped()}

    return {"verdict": "ok",
            "reason": (f"{len(audited)} Ethernet/wireless "
                      f"iface(s) audited ; no CRC, no tx_errors,"
                      f" no flapping, no elevated drops."),
            "recommendation": ""}


def status(config=None, sys_net: str = _SYS_NET) -> dict:
    present = os.path.isdir(sys_net)
    ifaces: Dict[str, dict] = {}
    if present:
        for n in list_interfaces(sys_net):
            ifaces[n] = read_iface_stats(sys_net, n)
    verdict = classify(present, ifaces)
    return {"ok": present,
              "iface_count": len(ifaces),
              "ifaces": list(ifaces.keys()),
              "stats": ifaces,
              "verdict": verdict}


# ── recovery recipes ────────────────────────────────────────────

def _recipe_crc() -> str:
    return ("# rx_crc_errors > 0 means physical-layer corruption.\n"
            "# Inspect counts :\n"
            "for s in /sys/class/net/*/statistics/rx_crc_errors; do\n"
            "  n=$(cat \"$s\")\n"
            "  [ \"$n\" -gt 0 ] && echo \"$s = $n\"\n"
            "done\n"
            "# Swap the Ethernet cable / patch panel jack.\n"
            "# Verify with :  ethtool -S <iface> | grep -i crc\n")


def _recipe_tx_errors() -> str:
    return ("# tx_errors usually = upstream switch reject or\n"
            "# carrier-loss mid-frame. Investigate :\n"
            "for s in /sys/class/net/*/statistics/tx_errors; do\n"
            "  n=$(cat \"$s\")\n"
            "  [ \"$n\" -gt 0 ] && echo \"$s = $n\"\n"
            "done\n"
            "# ethtool <iface>  — confirm speed / duplex\n"
            "# Check switch port for errors too.\n")


def _recipe_flap() -> str:
    return ("# carrier_changes > 100 = link is flapping.\n"
            "# Watch live :\n"
            "watch -n2 cat /sys/class/net/*/carrier_changes\n"
            "# Common causes : autoneg fail, PoE budget exceeded,\n"
            "# bad cable, switch port stuck in spanning-tree.\n")


def _recipe_dropped() -> str:
    return ("# Elevated rx_dropped ratio. Inspect :\n"
            "for d in /sys/class/net/*/statistics; do\n"
            "  p=$(cat $d/rx_packets) ; r=$(cat $d/rx_dropped)\n"
            "  echo \"$d : dropped=$r / packets=$p\"\n"
            "done\n"
            "# Raise socket buffers if app drops :\n"
            "sudo sysctl -w net.core.rmem_max=33554432\n"
            "# Check rx ring size :  ethtool -g <iface>\n")
