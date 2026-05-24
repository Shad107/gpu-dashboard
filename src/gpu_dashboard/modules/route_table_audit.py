"""Module route_table_audit — IPv4/IPv6 routing-table
sanity check (R&D #79.2).

Reads /proc/net/route and /proc/net/ipv6_route, decodes the
hex-little-endian (v4) and hex-network-order (v6) fields,
and flags routing-table drift that breaks homelab networking
quietly :

- Default gateway gone after a NetworkManager reload.
- Two conflicting default routes on different ifaces with
  the same metric (kernel picks one arbitrarily — flaky).
- Default gateway on a container-bridge iface (docker0,
  virbr0, vboxnet0) — common when a misconfigured container
  steals the host's default.
- Excessive /32 host routes (often a VPN-client leak that
  adds bypass routes around the tunnel).

NOT flagged on purpose : VPN tunnel ifaces as the default
(wg0/tun0/tap0) — that's the typical full-tunnel VPN setup.

Verdicts (worst first) :

  err     no IPv4 default gw  OR  ≥2 default gws on diff
          ifaces with same metric (split-routing).
  warn    default gw on container-bridge iface  OR
          ≥3 default routes total.
  accent  > 50 /32 host routes (VPN-leak signal).
  ok      single sane default + reasonable host-route count.
  unknown /proc/net/route missing.
"""
from __future__ import annotations

import os
import re
from typing import Optional

DEFAULT_V4_PATH = "/proc/net/route"
DEFAULT_V6_PATH = "/proc/net/ipv6_route"

# Container-bridge iface prefixes — default gw here is
# almost always a misconfig / hijack.
_CONTAINER_BRIDGE_RE = re.compile(
    r"^(docker\w*|virbr\d+|vboxnet\d+|vmnet\d+|"
    r"br-[0-9a-f]{10,})$")

# Thresholds
_HOST_ROUTE_FLOOR = 50  # /32 count above which we flag


def _parse_le_ip4(hex_str: str) -> Optional[str]:
    if len(hex_str) != 8:
        return None
    try:
        b = bytes.fromhex(hex_str)
    except ValueError:
        return None
    return f"{b[3]}.{b[2]}.{b[1]}.{b[0]}"


def _parse_ip6(hex_str: str) -> Optional[str]:
    if len(hex_str) != 32:
        return None
    try:
        b = bytes.fromhex(hex_str)
    except ValueError:
        return None
    parts = [f"{b[i]:02x}{b[i+1]:02x}" for i in range(0, 16, 2)]
    return ":".join(parts)


def parse_v4(text: str) -> list[dict]:
    """Parse /proc/net/route format."""
    rows: list[dict] = []
    for i, line in enumerate(text.splitlines()):
        if i == 0:  # header
            continue
        cols = line.split()
        if len(cols) < 8:
            continue
        try:
            metric = int(cols[6])
            mask = cols[7]
        except (ValueError, IndexError):
            continue
        rows.append({
            "iface": cols[0],
            "destination_raw": cols[1],
            "destination": _parse_le_ip4(cols[1]),
            "gateway_raw": cols[2],
            "gateway": _parse_le_ip4(cols[2]),
            "flags": int(cols[3], 16) if cols[3] else 0,
            "metric": metric,
            "mask": mask,
            "mask_decoded": _parse_le_ip4(mask),
        })
    return rows


def parse_v6(text: str) -> list[dict]:
    """Parse /proc/net/ipv6_route format."""
    rows: list[dict] = []
    for line in text.splitlines():
        cols = line.split()
        if len(cols) < 10:
            continue
        try:
            prefix_len = int(cols[1], 16)
        except ValueError:
            continue
        rows.append({
            "destination_raw": cols[0],
            "destination": _parse_ip6(cols[0]),
            "prefix_len": prefix_len,
            "iface": cols[-1],
        })
    return rows


def read_v4(path: str = DEFAULT_V4_PATH) -> Optional[list[dict]]:
    try:
        with open(path, "r", encoding="utf-8") as fh:
            return parse_v4(fh.read())
    except (OSError, PermissionError):
        return None


def read_v6(path: str = DEFAULT_V6_PATH) -> Optional[list[dict]]:
    try:
        with open(path, "r", encoding="utf-8") as fh:
            return parse_v6(fh.read())
    except (OSError, PermissionError):
        return None


def _is_default_v4(row: dict) -> bool:
    return (row["destination_raw"] == "00000000"
            and row["mask"] == "00000000")


def _is_host_route_v4(row: dict) -> bool:
    return row["mask"] == "FFFFFFFF"


def classify(v4: Optional[list[dict]],
             v6: Optional[list[dict]]) -> dict:
    if v4 is None:
        return {"verdict": "unknown",
                "reason": "/proc/net/route unreadable."}

    defaults = [r for r in v4 if _is_default_v4(r)]
    host_routes = [r for r in v4 if _is_host_route_v4(r)]

    # 1. err — no default gw
    if not defaults:
        return {"verdict": "err",
                "reason": "No IPv4 default gateway present."}

    # 1b. err — ≥2 defaults with same metric on diff ifaces
    by_metric: dict[int, set[str]] = {}
    for d in defaults:
        by_metric.setdefault(d["metric"], set()).add(d["iface"])
    conflicting = [(m, ifs) for m, ifs in by_metric.items()
                    if len(ifs) > 1]
    if conflicting:
        m, ifs = conflicting[0]
        return {"verdict": "err",
                "reason": (
                    f"{len(ifs)} default gw(s) with metric {m} "
                    f"on diff ifaces: {','.join(sorted(ifs))} "
                    "— kernel picks arbitrarily."),
                "conflicting_ifaces": sorted(ifs)}

    # 2. warn — default gw on container bridge
    container_defaults = [
        d for d in defaults
        if _CONTAINER_BRIDGE_RE.match(d["iface"])]
    if container_defaults:
        d = container_defaults[0]
        return {"verdict": "warn",
                "reason": (
                    f"Default gw on container-bridge iface "
                    f"{d['iface']} via {d['gateway']} — likely "
                    "a misconfigured container that stole the "
                    "host default."),
                "iface": d["iface"],
                "gateway": d["gateway"]}

    # 2b. warn — many defaults
    if len(defaults) >= 3:
        return {"verdict": "warn",
                "reason": (
                    f"{len(defaults)} default routes present "
                    "— more than expected on a homelab box."),
                "default_count": len(defaults)}

    # 3. accent — many /32 host routes
    if len(host_routes) > _HOST_ROUTE_FLOOR:
        return {"verdict": "accent",
                "reason": (
                    f"{len(host_routes)} /32 host routes — "
                    "possible VPN-client bypass leak."),
                "host_route_count": len(host_routes)}

    # 4. ok
    primary = defaults[0]
    return {"verdict": "ok",
            "reason": (
                f"1 default gw via {primary['gateway']} on "
                f"{primary['iface']} ; "
                f"{len(host_routes)} host route(s)."),
            "default_iface": primary["iface"],
            "default_gateway": primary["gateway"]}


def status(config: Optional[dict] = None,
           v4_path: str = DEFAULT_V4_PATH,
           v6_path: str = DEFAULT_V6_PATH) -> dict:
    v4 = read_v4(v4_path)
    v6 = read_v6(v6_path)
    verdict = classify(v4, v6)
    return {
        "ok": verdict["verdict"] not in (
            "err", "unknown"),
        "v4_route_count": len(v4) if v4 else 0,
        "v6_route_count": len(v6) if v6 else 0,
        "default_v4_count": sum(
            1 for r in (v4 or []) if _is_default_v4(r)),
        "host_v4_count": sum(
            1 for r in (v4 or []) if _is_host_route_v4(r)),
        "verdict": verdict,
    }
