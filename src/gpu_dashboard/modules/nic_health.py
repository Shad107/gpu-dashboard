"""Module nic_health — LAN NIC health correlator (R&D #33.1).

Inference daemons (ollama, llama-server, ComfyUI) are increasingly
served over LAN — a desktop GPU rig serving a laptop client at the
other end of the house. Latency spikes there usually trace back to:

  - Link flap on the wired NIC (carrier=0 transients)
  - Driver / ring-buffer drops (rx_dropped, tx_dropped)
  - Sub-gigabit auto-neg fallback (speed=100 on a wired card)
  - Hardware errors (rx_errors, tx_errors → cable / PHY fault)

This module enumerates /sys/class/net/<dev>/, filters to physical /
relevant interfaces (skips lo, docker*, virbr*, br-*, veth*, tap*),
reads carrier + operstate + speed + statistics/{rx,tx}_{bytes,
dropped,errors}, and classifies each:

  link_down       carrier=0 + operstate=down (or != up)
  errors_present  rx_errors > 0 OR tx_errors > 0 (PHY / cable)
  drops_high      rx_dropped + tx_dropped >= 1000 (ring buffer too
                  small, or LAN congestion)
  speed_low       speed > 0 AND < 1000 Mbps (sub-gigabit auto-neg)
  clean           none of the above
  unknown         carrier file unreadable

stdlib only.
"""
from __future__ import annotations

import os
import re
from typing import Optional


NAME = "nic_health"


_NET_ROOT = "/sys/class/net"


# Drop thresholds — picked conservatively so transient single-digit
# drops don't fire warnings.
_DROPS_WARN = 1000           # accumulated drops to flag
_ERRORS_WARN = 1             # any frame error is news
_SPEED_LOW_MBPS = 1000       # warn below gigabit on a wired card


# Names we never bubble up — bridges / docker / virtual pair members
# Order matters; longest prefix first.
_SKIP_PATTERNS = (
    "lo",
    "docker", "br-", "virbr",
    "veth", "tap", "vnet", "macvtap",
    "wg",
    "fwbr",      # Proxmox firewall bridges
    "fwln", "fwpr",
)


def is_relevant(name: str, type_: Optional[str] = None) -> bool:
    if not name:
        return False
    if name == "lo":
        return False
    for pat in _SKIP_PATTERNS:
        if name.startswith(pat):
            return False
    return True


def _read(p: str) -> Optional[str]:
    try:
        with open(p) as f:
            return f.read().strip()
    except OSError:
        return None


def read_attr(root: str, iface: str, attr: str) -> Optional[str]:
    return _read(os.path.join(root, iface, attr))


def read_stat(root: str, iface: str, stat: str) -> Optional[int]:
    s = _read(os.path.join(root, iface, "statistics", stat))
    if s is None:
        return None
    try:
        return int(s)
    except ValueError:
        return None


def list_interfaces(root: str = _NET_ROOT) -> list:
    try:
        names = sorted(os.listdir(root))
    except OSError:
        return []
    out: list = []
    for n in names:
        if not os.path.isdir(os.path.join(root, n)):
            continue
        type_ = _read(os.path.join(root, n, "type"))
        if not is_relevant(n, type_):
            continue
        out.append(n)
    return out


_RANK = {
    "clean": 0,
    "unknown": 1,
    "speed_low": 2,
    "drops_high": 3,
    "errors_present": 4,
    "link_down": 4,
}


def classify(rec: dict) -> dict:
    if rec.get("carrier") is None:
        return {"verdict": "unknown",
                "reason": "Could not read carrier / statistics.",
                "recommendation": ""}
    carrier = rec.get("carrier") == "1"
    operstate = (rec.get("operstate") or "").lower()
    if not carrier or operstate not in ("up", "unknown"):
        return {"verdict": "link_down",
                "reason": (f"{rec['name']} carrier={rec.get('carrier')} "
                           f"operstate={rec.get('operstate')} — link is "
                           f"not carrying traffic."),
                "recommendation": (
                    f"# Check cable + remote end:\n"
                    f"ethtool {rec['name']}              # link status\n"
                    f"ip -s link show {rec['name']}       # rx/tx counters"
                )}
    rx_err = rec.get("rx_errors") or 0
    tx_err = rec.get("tx_errors") or 0
    if (rx_err + tx_err) >= _ERRORS_WARN:
        return {"verdict": "errors_present",
                "reason": (f"{rec['name']} rx_errors={rx_err} "
                           f"tx_errors={tx_err} — frame errors signal "
                           f"a cable, PHY, or driver fault."),
                "recommendation": (
                    f"# Frame errors → physical layer suspect:\n"
                    f"ethtool -S {rec['name']} | grep -Ei 'err|crc'\n"
                    f"# Swap cable, reseat connector, "
                    f"or try a known-good port."
                )}
    rx_drop = rec.get("rx_dropped") or 0
    tx_drop = rec.get("tx_dropped") or 0
    if (rx_drop + tx_drop) >= _DROPS_WARN:
        return {"verdict": "drops_high",
                "reason": (f"{rec['name']} rx_dropped={rx_drop} "
                           f"tx_dropped={tx_drop} — packets discarded "
                           f"before reaching userspace. Ring-buffer is "
                           f"too small or LAN is congested."),
                "recommendation": (
                    f"# Inspect current ring sizes:\n"
                    f"ethtool -g {rec['name']}\n"
                    f"# Bump to the listed max (e.g. 4096):\n"
                    f"sudo ethtool -G {rec['name']} rx 4096 tx 4096\n"
                    f"# Persist via NetworkManager or /etc/network/interfaces"
                )}
    speed = rec.get("speed")
    if (speed is not None and speed > 0 and speed < _SPEED_LOW_MBPS):
        return {"verdict": "speed_low",
                "reason": (f"{rec['name']} negotiated {speed} Mbps — "
                           f"below gigabit. Cable / switch port might "
                           f"be capped."),
                "recommendation": (
                    f"# Confirm capability vs negotiated:\n"
                    f"ethtool {rec['name']} | grep -E 'Supported|Advertis|Speed'\n"
                    f"# Bad cable or duplex mismatch is the usual cause."
                )}
    return {"verdict": "clean",
            "reason": (f"{rec['name']} up at {speed if speed and speed > 0 else '—'} "
                       f"Mbps, no drops or errors."),
            "recommendation": ""}


def status(cfg=None) -> dict:
    ifaces = list_interfaces(_NET_ROOT)
    if not ifaces:
        return {"ok": True, "interface_count": 0,
                "interfaces": [],
                "worst_verdict": "no_nics",
                "total_rx_bytes": 0, "total_tx_bytes": 0}
    out: list = []
    worst = "clean"
    total_rx = total_tx = 0
    for n in ifaces:
        speed_s = read_attr(_NET_ROOT, n, "speed")
        try:
            speed = int(speed_s) if speed_s is not None else None
        except ValueError:
            speed = None
        rx_b = read_stat(_NET_ROOT, n, "rx_bytes")
        tx_b = read_stat(_NET_ROOT, n, "tx_bytes")
        rec = {
            "name": n,
            "carrier": read_attr(_NET_ROOT, n, "carrier"),
            "operstate": read_attr(_NET_ROOT, n, "operstate"),
            "speed": speed,
            "rx_bytes": rx_b,
            "tx_bytes": tx_b,
            "rx_dropped": read_stat(_NET_ROOT, n, "rx_dropped"),
            "tx_dropped": read_stat(_NET_ROOT, n, "tx_dropped"),
            "rx_errors": read_stat(_NET_ROOT, n, "rx_errors"),
            "tx_errors": read_stat(_NET_ROOT, n, "tx_errors"),
        }
        v = classify(rec)
        if _RANK.get(v["verdict"], 0) > _RANK.get(worst, 0):
            worst = v["verdict"]
        rec["verdict"] = v
        total_rx += rx_b or 0
        total_tx += tx_b or 0
        out.append(rec)
    return {"ok": True, "interface_count": len(out),
            "interfaces": out, "worst_verdict": worst,
            "total_rx_bytes": total_rx, "total_tx_bytes": total_tx}
