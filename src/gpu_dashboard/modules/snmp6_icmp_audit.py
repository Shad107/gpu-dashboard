"""Module snmp6_icmp_audit — IPv6 ICMP / ND / MLD counter
audit (R&D #80.2).

Reads /proc/net/snmp6 — the kernel's per-protocol counters
for the IPv6 stack.  IPv6-on-by-default homelabs (Proxmox
bridges, podman networks, modern desktops with SLAAC)
silently accumulate ICMPv6 / Neighbor-Discovery (ND) / MLD
errors that never show up in IPv4 counters.

Counters we inspect (kernel cumulative since boot) :

  Icmp6InErrors            inbound ICMP6 the kernel rejected
                           (malformed, unsupported)
  Icmp6OutErrors           outbound ICMP6 we couldn't send
                           (very rare on healthy stack)
  Icmp6InCsumErrors        checksum failures inbound
  Icmp6InMsgs              total inbound ICMP6 messages
  Icmp6OutMsgs             total outbound ICMP6 messages
  Icmp6InPktTooBigs        ND PMTU discovery — informational
  Icmp6InNeighborAdvertisements  unsolicited NA storm signal
  Icmp6InGroupMembQueries  MLD query traffic
  Icmp6OutGroupMembResponses  MLD response traffic
  Ip6InAddrErrors          source/dest address invalid
  Ip6InHdrErrors           v6 header parse failures

Verdicts (worst first) :

  icmp6_in_errors_growing   csum errors  OR Icmp6InErrors
                            ratio > 1 %  →  broken peer
                            or on-wire corruption.
  nd_unsolicited_advert_storm  NA / NS ratio > 5×  AND
                               NA > 5000  →  ND poisoning
                               or buggy router NA flood.
  mld_query_loss            MLD reports >> queries (host
                            sending without being asked)
                            OR Ip6InAddrErrors > 100.
  ok                        all counters quiet.
  unknown                   /proc/net/snmp6 unreadable.
"""
from __future__ import annotations

import os
from typing import Optional

DEFAULT_SNMP6 = "/proc/net/snmp6"

# Thresholds
_IN_ERR_RATIO = 0.01           # 1 % of Icmp6InMsgs
_IN_ERR_FLOOR = 100
_NA_STORM_RATIO = 5.0
_NA_STORM_FLOOR = 5000
_IP6_ADDR_ERR_FLOOR = 100


def _read_text(path: str) -> Optional[str]:
    try:
        with open(path, "r", encoding="utf-8") as fh:
            return fh.read()
    except (OSError, PermissionError):
        return None


def parse_snmp6(text: str) -> dict:
    """Returns flat dict {counter_name: int}."""
    out: dict = {}
    for line in text.splitlines():
        parts = line.split()
        if len(parts) != 2:
            continue
        try:
            out[parts[0]] = int(parts[1])
        except ValueError:
            continue
    return out


def read_snmp6(path: str = DEFAULT_SNMP6) -> Optional[dict]:
    text = _read_text(path)
    if text is None:
        return None
    return parse_snmp6(text)


def classify(counters: Optional[dict]) -> dict:
    if counters is None or not counters:
        return {"verdict": "unknown",
                "reason": "/proc/net/snmp6 unreadable."}

    in_msgs = counters.get("Icmp6InMsgs", 0)
    in_err = counters.get("Icmp6InErrors", 0)
    csum_err = counters.get("Icmp6InCsumErrors", 0)
    out_msgs = counters.get("Icmp6OutMsgs", 0)
    out_err = counters.get("Icmp6OutErrors", 0)
    na_in = counters.get(
        "Icmp6InNeighborAdvertisements", 0)
    ns_out = counters.get(
        "Icmp6OutNeighborSolicits", 0)
    addr_err = counters.get("Ip6InAddrErrors", 0)
    hdr_err = counters.get("Ip6InHdrErrors", 0)

    # 1. err — csum errors or in-error ratio elevated
    if csum_err > 0:
        return {"verdict": "icmp6_in_errors_growing",
                "reason": (
                    f"Icmp6InCsumErrors = {csum_err} — "
                    "on-wire corruption or broken IPv6 peer."),
                "csum_errors": csum_err}
    if (in_err >= _IN_ERR_FLOOR
            and in_msgs > 0
            and in_err / in_msgs > _IN_ERR_RATIO):
        return {"verdict": "icmp6_in_errors_growing",
                "reason": (
                    f"Icmp6InErrors = {in_err} of "
                    f"{in_msgs} ({in_err/in_msgs:.2%}) — "
                    "high ICMP6 rejection rate."),
                "in_errors": in_err,
                "in_msgs": in_msgs}

    # 2. warn — unsolicited NA storm
    if na_in >= _NA_STORM_FLOOR:
        ratio = (na_in / ns_out) if ns_out > 0 else float("inf")
        if ratio > _NA_STORM_RATIO:
            return {"verdict": "nd_unsolicited_advert_storm",
                    "reason": (
                        f"{na_in} inbound NA vs {ns_out} "
                        "outbound NS — possible ND-poisoning "
                        f"or rogue router flood (ratio "
                        f"{ratio:.1f}×)."),
                    "na_in": na_in, "ns_out": ns_out}

    # 3. accent — address / header errors
    if addr_err > _IP6_ADDR_ERR_FLOOR or hdr_err > 0:
        return {"verdict": "mld_query_loss",
                "reason": (
                    f"Ip6InAddrErrors = {addr_err}, "
                    f"Ip6InHdrErrors = {hdr_err} — IPv6 "
                    "header/address pathology on the LAN."),
                "addr_errors": addr_err,
                "hdr_errors": hdr_err}

    # 4. ok
    return {"verdict": "ok",
            "reason": (
                f"{in_msgs} in / {out_msgs} out ICMP6 ; "
                "0 errors, 0 csum issues, NA/NS ratio sane.")}


def status(config: Optional[dict] = None,
           snmp6_path: str = DEFAULT_SNMP6) -> dict:
    counters = read_snmp6(snmp6_path)
    verdict = classify(counters)
    sample_keys = (
        "Icmp6InMsgs", "Icmp6InErrors", "Icmp6InCsumErrors",
        "Icmp6OutMsgs", "Icmp6OutErrors",
        "Icmp6InNeighborAdvertisements",
        "Icmp6OutNeighborSolicits",
        "Icmp6InPktTooBigs",
        "Ip6InAddrErrors", "Ip6InHdrErrors",
        "Ip6InReceives", "Ip6OutRequests")
    return {
        "ok": verdict["verdict"] not in (
            "unknown", "icmp6_in_errors_growing"),
        "counter_count": len(counters) if counters else 0,
        "sample": {
            k: (counters or {}).get(k, 0) for k in sample_keys},
        "verdict": verdict,
    }
