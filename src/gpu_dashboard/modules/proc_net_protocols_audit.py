"""Module proc_net_protocols_audit — AF_PACKET + raw socket
leak detector (R&D #90.2).

Three existing modules read /proc/net aggregates :

  * sock_pool_audit   — sockstat / per-process socket totals
  * net_proto_counters — snmp / netstat counters
  * unix_socket_inventory_audit — /proc/net/unix

None touch /proc/net/{protocols,packet,raw,raw6}. This
audit owns those four files.

The most useful actionable signal :

  * an AF_PACKET socket bound with proto == ETH_P_ALL (0x0003)
    is the kernel-level signature of tcpdump / Wireshark
    capturing every frame — easy to forget running for days
    and a real softirq-budget hog on a busy NIC.
  * an unusual number of raw / raw6 sockets is the signature
    of an abandoned ICMP probe loop, suricata, or an exit
    daemon that died holding its socket.

Reads :

  /proc/net/protocols   header + one row per kernel proto
  /proc/net/packet      one row per AF_PACKET socket
  /proc/net/raw         one row per IPv4 raw socket
  /proc/net/raw6        one row per IPv6 raw socket

Verdicts (worst-first) :

  af_packet_promisc_listener  warn  ≥1 AF_PACKET socket with
                                    Proto field "0003"
                                    (ETH_P_ALL).
  raw_socket_leak             warn  total raw + raw6 socket
                                    count > 4 (heuristic).
  ok                          counts within normal envelope.
  unknown                     /proc/net/packet unreadable.

The proposed "protocol_high_memory" accent verdict was
dropped — MPTCPv6 / SCTPv6 routinely show non-zero memory
on stock kernels, so the signal is too noisy to surface.

stdlib only.
"""
from __future__ import annotations

import os
from typing import Optional

NAME = "proc_net_protocols_audit"

DEFAULT_PROC_NET = "/proc/net"

# Heuristic threshold for raw_socket_leak — anything above 4
# is a strong "abandoned tool" signal on a desktop / homelab.
_RAW_LEAK_THRESHOLD = 4


def _read_text(path: str) -> Optional[str]:
    try:
        with open(path, "r", encoding="utf-8") as fh:
            return fh.read()
    except (OSError, PermissionError):
        return None


def parse_packet(text: str) -> list:
    """Parse /proc/net/packet rows. Returns list of dicts
    with at least 'proto' key (hex string)."""
    if not text:
        return []
    out: list = []
    lines = text.splitlines()
    # First line is the header.
    for line in lines[1:]:
        parts = line.split()
        if len(parts) < 4:
            continue
        # Columns : sk RefCnt Type Proto Iface R Rmem User Inode
        out.append({
            "ref_count": parts[1],
            "type": parts[2],
            "proto": parts[3],
        })
    return out


def count_raw(text: str) -> int:
    """Count /proc/net/raw{,6} rows (excluding header)."""
    if not text:
        return 0
    lines = [ln for ln in text.splitlines() if ln.strip()]
    return max(0, len(lines) - 1)


def classify(packet_rows: list, raw4: int,
             raw6: int) -> dict:
    if not packet_rows and raw4 == 0 and raw6 == 0:
        # All three files came back empty — likely no procfs.
        return {"verdict": "unknown",
                "reason": (
                    "/proc/net/{packet,raw,raw6} appear empty "
                    "or unreadable.")}

    promisc = [
        r for r in packet_rows
        if r.get("proto", "").lower() == "0003"]
    if promisc:
        return {
            "verdict": "af_packet_promisc_listener",
            "reason": (
                f"{len(promisc)} AF_PACKET socket(s) bound "
                "with proto=ETH_P_ALL (0x0003) — signature of "
                "tcpdump / Wireshark capturing every frame. "
                "Check `ss -lpn` and `lsof | grep PACKET`."),
            "promisc_count": len(promisc),
        }

    raw_total = raw4 + raw6
    if raw_total > _RAW_LEAK_THRESHOLD:
        return {
            "verdict": "raw_socket_leak",
            "reason": (
                f"{raw_total} raw socket(s) open (raw4={raw4}, "
                f"raw6={raw6}) — above threshold "
                f"{_RAW_LEAK_THRESHOLD}. Likely an abandoned "
                "ICMP / ping / suricata process."),
            "raw_total": raw_total,
        }

    return {"verdict": "ok",
            "reason": (
                f"{len(packet_rows)} AF_PACKET socket(s) "
                "(none ETH_P_ALL), "
                f"{raw_total} raw socket(s) — normal envelope.")}


def status(config: Optional[dict] = None,
           proc_net: str = DEFAULT_PROC_NET) -> dict:
    packet_text = _read_text(
        os.path.join(proc_net, "packet")) or ""
    raw_text = _read_text(
        os.path.join(proc_net, "raw")) or ""
    raw6_text = _read_text(
        os.path.join(proc_net, "raw6")) or ""
    packet_rows = parse_packet(packet_text)
    raw4 = count_raw(raw_text)
    raw6 = count_raw(raw6_text)
    verdict = classify(packet_rows, raw4, raw6)
    return {
        "ok": verdict["verdict"] == "ok",
        "packet_socket_count": len(packet_rows),
        "raw_socket_count": raw4 + raw6,
        "verdict": verdict,
    }
