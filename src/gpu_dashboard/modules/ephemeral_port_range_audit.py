"""Module ephemeral_port_range_audit — TCP ephemeral port
window + utilisation (R&D #103.2).

Long-lived homelab boxes (Plex/Jellyfin/torrent clients +
llama.cpp loopback fan-out + container networking) eventually
run out of ephemeral source ports. The kernel then returns
EADDRNOTAVAIL on connect() and the user sees mysterious
'connection failed' errors.

Three sysctls matter :

  net.ipv4.ip_local_port_range        lo hi (window size)
  net.ipv4.ip_local_reserved_ports    comma-separated reserves
  net.ipv4.ip_unprivileged_port_start unprivileged-bind floor

Plus current usage from /proc/net/tcp{,6} (line count).

Existing modules don't touch this. net_sysctl_audit covers
reverse-path / tcp_*mem ; sock_pool_audit covers per-process
fd / socket counts ; tcp_congestion_control_audit owns BBR /
cubic / TFO ; net_proto_counters reads SNMP aggregates.

Verdicts (worst-first) :

  ephemeral_pool_exhausted    err     used / window > 80 %.
  port_window_too_small       warn    window < 16384 ports.
  unpriv_port_below_1024      accent  ip_unprivileged_port_start
                                      < 1024 — non-default,
                                      lets unpriv users bind
                                      historically-reserved
                                      ports.
  ok                                  wide window, low usage.
  requires_root                       /proc/net/tcp unreadable.
  unknown                             port_range sysctl absent.

stdlib only.
"""
from __future__ import annotations

import os
from typing import Optional

NAME = "ephemeral_port_range_audit"

DEFAULT_IPV4 = "/proc/sys/net/ipv4"
DEFAULT_TCP = "/proc/net/tcp"
DEFAULT_TCP6 = "/proc/net/tcp6"

_WINDOW_MIN = 16384
_UTIL_ERR_RATIO = 0.80


def _read_text(path: str) -> Optional[str]:
    try:
        with open(path, "r", encoding="utf-8") as fh:
            return fh.read()
    except (OSError, PermissionError):
        return None


def _read_int(path: str) -> Optional[int]:
    t = _read_text(path)
    if t is None:
        return None
    try:
        return int(t.strip())
    except ValueError:
        return None


def parse_port_range(text: Optional[str]
                       ) -> Optional[tuple]:
    """Parse 'lo\\thi' → (lo, hi)."""
    if not text:
        return None
    parts = text.split()
    if len(parts) != 2:
        return None
    try:
        return (int(parts[0]), int(parts[1]))
    except ValueError:
        return None


def count_tcp_sockets(text: Optional[str]) -> int:
    """Return number of socket rows (line count - 1 header)."""
    if not text:
        return 0
    lines = [ln for ln in text.splitlines() if ln.strip()]
    return max(0, len(lines) - 1)


def classify(ipv4_present: bool,
             port_range: Optional[tuple],
             unpriv_start: Optional[int],
             tcp_readable: bool,
             socket_count: int) -> dict:
    if not ipv4_present:
        return {"verdict": "unknown",
                "reason": (
                    "/proc/sys/net/ipv4 absent — no IPv4 "
                    "stack exposed.")}
    if port_range is None:
        return {"verdict": "requires_root",
                "reason": (
                    "ip_local_port_range unreadable — "
                    "re-run as root.")}

    lo, hi = port_range
    window = max(0, hi - lo + 1)

    # err — pool exhausted
    if (tcp_readable and window > 0
            and socket_count / window > _UTIL_ERR_RATIO):
        return {
            "verdict": "ephemeral_pool_exhausted",
            "reason": (
                f"{socket_count} TCP sockets vs "
                f"{window}-port ephemeral window — "
                f"{socket_count / window:.1%} used. "
                "Next connect() likely EADDRNOTAVAIL.")}

    # warn — window too small
    if window < _WINDOW_MIN:
        return {
            "verdict": "port_window_too_small",
            "reason": (
                f"ephemeral port range {lo}-{hi} = "
                f"{window} ports (< {_WINDOW_MIN}). "
                "Widen with sysctl on a heavy fan-out "
                "host.")}

    # accent — unpriv start < 1024
    if (unpriv_start is not None
            and 0 < unpriv_start < 1024):
        return {
            "verdict": "unpriv_port_below_1024",
            "reason": (
                f"ip_unprivileged_port_start="
                f"{unpriv_start} (< 1024) — unprivileged "
                "processes can bind historically-reserved "
                "ports. Intentional for rootless "
                "containers, surprising otherwise.")}

    return {"verdict": "ok",
            "reason": (
                f"window={window} ports ({lo}-{hi}) ; "
                f"used={socket_count} ; "
                f"unpriv_start={unpriv_start}. Healthy.")}


def status(config: Optional[dict] = None,
           ipv4: str = DEFAULT_IPV4,
           tcp: str = DEFAULT_TCP,
           tcp6: str = DEFAULT_TCP6) -> dict:
    ipv4_present = os.path.isdir(ipv4)
    port_range = parse_port_range(
        _read_text(os.path.join(ipv4, "ip_local_port_range"))
        if ipv4_present else None)
    unpriv_start = (
        _read_int(os.path.join(
            ipv4, "ip_unprivileged_port_start"))
        if ipv4_present else None)
    reserved = (
        _read_text(os.path.join(
            ipv4, "ip_local_reserved_ports"))
        if ipv4_present else None)
    tcp_text = _read_text(tcp)
    tcp6_text = _read_text(tcp6)
    tcp_readable = tcp_text is not None
    socket_count = (
        count_tcp_sockets(tcp_text)
        + count_tcp_sockets(tcp6_text))
    verdict = classify(ipv4_present, port_range,
                       unpriv_start, tcp_readable,
                       socket_count)
    return {
        "ok": verdict["verdict"] == "ok",
        "port_range_lo": port_range[0] if port_range else None,
        "port_range_hi": port_range[1] if port_range else None,
        "port_window": (
            (port_range[1] - port_range[0] + 1)
            if port_range else None),
        "ip_unprivileged_port_start": unpriv_start,
        "reserved_ports": (
            reserved.strip() if reserved else ""),
        "tcp_socket_count": socket_count,
        "verdict": verdict,
    }
