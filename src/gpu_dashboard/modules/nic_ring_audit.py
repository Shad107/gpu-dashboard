"""Module nic_ring_audit — NIC ring-buffer drop monitor (R&D #43.4).

Shipped #40.4 nic_queue_affinity covers the *spread* axis (RPS /
XPS / RFS masks). This module covers the orthogonal *drop* axis :
when the ring buffer overruns, the NIC silently discards packets
*before* the kernel can route them — the symptom is "inference
clients sometimes time out under fan-out load with no obvious
cause".

The kernel exposes /sys/class/net/<dev>/statistics/* :
  rx_dropped         packets dropped by the kernel after the driver
                     delivered them (queue overflow, memory pressure)
  rx_fifo_errors     hardware FIFO overrun on the NIC itself — ring
                     buffer too small for the line rate
  rx_missed_errors   like rx_fifo_errors but reported by some drivers
                     under a different counter ; sum them
  rx_errors          aggregate RX error count
  rx_crc_errors      CRC failures (cable issue / bad transceiver)
  rx_frame_errors    framing errors (cable issue / duplex mismatch)
  rx_packets         total RX
  tx_dropped         TX queue drops — typically harmless for an idle
                     interface (Docker bridge with no consumer
                     accumulates these) but meaningful for an up
                     consumer-facing NIC
  tx_fifo_errors     hardware TX FIFO overrun

Verdicts (per-device, then worst-of across UP devices) :
  fifo_overrun           rx_fifo_errors + rx_missed_errors > 0 on a
                         UP device — hardware ring too small ;
                         increase via `ethtool -G <iface> rx N`.
  rx_drops_climbing      rx_dropped / rx_packets > 0.1 % on a UP
                         device with > 10k RX packets — the kernel
                         is dropping packets that reached the host.
  cable_or_duplex        rx_crc_errors > 0 or rx_frame_errors > 0
                         on a UP device — cable / SFP / duplex
                         mismatch.
  tx_drops               tx_dropped / tx_packets > 1 % on a UP
                         device — TX queue overrun.
  ok                     all counters quiet on UP devices.
  no_active_nic          every device is down.
  unknown                /sys/class/net unreadable.

stdlib only.
"""
from __future__ import annotations

import os
from typing import Optional


NAME = "nic_ring_audit"


_SYS_NET = "/sys/class/net"


_SKIP_DEVICES = ("lo", "bonding_masters")


_STATS_FIELDS = (
    "rx_dropped", "rx_fifo_errors", "rx_missed_errors",
    "rx_errors", "rx_crc_errors", "rx_frame_errors",
    "rx_packets", "rx_bytes",
    "tx_dropped", "tx_fifo_errors", "tx_errors",
    "tx_packets", "tx_bytes",
)


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
        return int(t.strip())
    except ValueError:
        return None


def list_devices(sys_net: str = _SYS_NET) -> list:
    if not os.path.isdir(sys_net):
        return []
    out: list = []
    for name in sorted(os.listdir(sys_net)):
        if name in _SKIP_DEVICES:
            continue
        if not os.path.isdir(os.path.join(sys_net, name)):
            continue
        out.append(name)
    return out


def read_device(sys_net: str, dev: str) -> dict:
    ddir = os.path.join(sys_net, dev)
    stats: dict = {}
    for f in _STATS_FIELDS:
        v = _read_int(os.path.join(ddir, "statistics", f))
        if v is not None:
            stats[f] = v
    return {
        "dev": dev,
        "operstate": (_read(os.path.join(ddir, "operstate"))
                       or "").strip(),
        "carrier": _read_int(os.path.join(ddir, "carrier")),
        "mtu": _read_int(os.path.join(ddir, "mtu")),
        **stats,
    }


def is_up(d: dict) -> bool:
    if d.get("operstate") == "up":
        return True
    return (d.get("carrier") == 1
            and d.get("operstate") not in ("down", "lowerlayerdown"))


_RX_DROP_RATIO_THRESHOLD = 0.001    # 0.1 %
_TX_DROP_RATIO_THRESHOLD = 0.01     # 1 %
_RX_PACKET_MIN_FOR_RATIO = 10_000   # don't classify off tiny samples


_RECIPE_RING_BUFFER = (
    "# Hardware RX FIFO overrun — the NIC ring buffer can't keep up\n"
    "# with the line rate. Check current vs max ring size :\n"
    "ethtool -g <IFACE>\n"
    "# Then bump to the max documented in the output above :\n"
    "sudo ethtool -G <IFACE> rx 4096\n"
    "# Persistent (NetworkManager + systemd-networkd vary) :\n"
    "# add a udev rule under /etc/udev/rules.d/99-nic-ring.rules :\n"
    "# ACTION==\"add\", SUBSYSTEM==\"net\", KERNEL==\"<IFACE>\", \\\n"
    "#  RUN+=\"/sbin/ethtool -G %k rx 4096\""
)

_RECIPE_RX_DROPS = (
    "# Kernel is dropping packets after the driver delivered them —\n"
    "# typically backlog overflow. Bump netdev_max_backlog and\n"
    "# enable RPS (see shipped #40.4 nic_queue_affinity) :\n"
    "echo 30000 | sudo tee /proc/sys/net/core/netdev_max_backlog\n"
    "echo 'net.core.netdev_max_backlog = 30000' | \\\n"
    "  sudo tee /etc/sysctl.d/99-netdev-backlog.conf"
)

_RECIPE_CABLE = (
    "# CRC / frame errors point at a cable / SFP / duplex issue.\n"
    "# Confirm with ethtool :\n"
    "ethtool <IFACE>\n"
    "ethtool -S <IFACE> | grep -iE 'err|drop|crc|fifo|miss'\n"
    "# If duplex mismatch :\n"
    "sudo ethtool -s <IFACE> autoneg on\n"
    "# Otherwise reseat the cable / try a known-good SFP."
)

_RECIPE_TX_DROPS = (
    "# TX queue overrun on a UP consumer-facing NIC. Bump qdisc :\n"
    "ip link set <IFACE> txqueuelen 10000\n"
    "# Persist via udev :\n"
    "# ACTION==\"add\", SUBSYSTEM==\"net\", KERNEL==\"<IFACE>\", \\\n"
    "#  ATTR{tx_queue_len}=\"10000\""
)


_RANK = {
    "ok": 0, "no_active_nic": 0,
    "tx_drops": 1, "cable_or_duplex": 2,
    "rx_drops_climbing": 3, "fifo_overrun": 4,
}


def classify(devices: list) -> dict:
    if not devices:
        return {"verdict": "unknown",
                "reason": "/sys/class/net unreadable.",
                "recommendation": ""}
    up = [d for d in devices if is_up(d)]
    if not up:
        return {"verdict": "no_active_nic",
                "reason": "No NIC currently up with link.",
                "recommendation": ""}
    best = {"verdict": "ok",
              "reason": (f"All {len(up)} up NIC(s) report clean ring + "
                         f"FIFO + CRC counters."),
              "recommendation": ""}
    for d in up:
        dev = d["dev"]
        fifo_total = ((d.get("rx_fifo_errors") or 0)
                       + (d.get("rx_missed_errors") or 0))
        rx_drop = d.get("rx_dropped") or 0
        rx_pkts = d.get("rx_packets") or 0
        crc = d.get("rx_crc_errors") or 0
        frame = d.get("rx_frame_errors") or 0
        tx_drop = d.get("tx_dropped") or 0
        tx_pkts = d.get("tx_packets") or 0
        if fifo_total > 0:
            cand = "fifo_overrun"
            cand_reason = (f"{dev} : rx_fifo_errors + "
                           f"rx_missed_errors = {fifo_total} — NIC "
                           f"ring buffer overruns. Hardware ring "
                           f"too small for line rate.")
            cand_recipe = _RECIPE_RING_BUFFER
        elif (rx_pkts >= _RX_PACKET_MIN_FOR_RATIO
              and rx_drop / rx_pkts >= _RX_DROP_RATIO_THRESHOLD):
            pct = rx_drop / rx_pkts * 100
            cand = "rx_drops_climbing"
            cand_reason = (f"{dev} : rx_dropped={rx_drop} of "
                           f"rx_packets={rx_pkts} ({pct:.2f} %). "
                           f"Backlog overflow ; kernel is dropping "
                           f"packets after the driver delivered them.")
            cand_recipe = _RECIPE_RX_DROPS
        elif crc > 0 or frame > 0:
            cand = "cable_or_duplex"
            cand_reason = (f"{dev} : rx_crc_errors={crc}, "
                           f"rx_frame_errors={frame}. Cable / SFP / "
                           f"duplex mismatch.")
            cand_recipe = _RECIPE_CABLE
        elif (tx_pkts >= _RX_PACKET_MIN_FOR_RATIO
              and tx_drop / tx_pkts >= _TX_DROP_RATIO_THRESHOLD):
            pct = tx_drop / tx_pkts * 100
            cand = "tx_drops"
            cand_reason = (f"{dev} : tx_dropped={tx_drop} of "
                           f"tx_packets={tx_pkts} ({pct:.2f} %). "
                           f"TX queue overrun.")
            cand_recipe = _RECIPE_TX_DROPS
        else:
            continue
        if _RANK.get(cand, 0) > _RANK.get(best["verdict"], 0):
            best = {"verdict": cand, "reason": cand_reason,
                     "recommendation": cand_recipe}
    return best


def status(cfg=None) -> dict:
    dev_names = list_devices(_SYS_NET)
    if not dev_names:
        return {
            "ok": False, "devices": [],
            "verdict": {"verdict": "unknown",
                         "reason": "/sys/class/net unreadable.",
                         "recommendation": ""},
        }
    devs = [read_device(_SYS_NET, n) for n in dev_names]
    verdict = classify(devs)
    return {
        "ok": True,
        "device_count": len(devs),
        "devices": devs,
        "verdict": verdict,
    }
