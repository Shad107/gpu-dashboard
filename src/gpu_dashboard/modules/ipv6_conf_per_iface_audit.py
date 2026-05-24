"""Module ipv6_conf_per_iface_audit — per-interface IPv6
networking knobs audit (R&D #76.2).

Mirror of the just-shipped ipv4_conf_per_iface_audit, for IPv6.
A single-GPU homelab box exposing a model server over IPv6 can :

* leak global temporary addresses (use_tempaddr ≠ 2) → privacy
  problem.
* accept rogue RAs on a LAN interface while ALSO running as a
  router (forwarding=1 + accept_ra=1) → RA-flood DoS surface.
* silently route via libvirt / docker bridge after a fresh
  install (forwarding=1) — unsolicited.
* leak ICMPv6 redirects (accept_redirects=1).

Reads (per iface + all/ + default/) :
  accept_ra              accept Router Advertisements
  autoconf               run SLAAC autoconfig
  forwarding             act as router
  disable_ipv6           1 = IPv6 off on this iface
  accept_redirects       1 = accept ICMPv6 redirect
  use_tempaddr           0=off 1=prefer-stable 2=prefer-tempaddr
  addr_gen_mode          0=EUI-64 1=stable-privacy ...
  router_solicitations   number of RS to send (-1 = forever)
  accept_source_route    1 = accept LSRR / SSRR (rare on v6)

Verdicts (priority order) :
  ra_accept_on_router         ≥1 iface has accept_ra=1 AND
                                forwarding=1 (RFC 4861 says
                                routers should NOT accept RAs).
  tempaddr_disabled_public    ≥1 non-lo iface use_tempaddr=0
                                (privacy extension off — DUID
                                may leak across networks).
  unsolicited_forwarding      all/forwarding=1 (host acts as
                                router — confirm intended).
  redirects_accepted          ≥1 non-lo non-all iface
                                accept_redirects=1.
  ok                          defaults sane.
  unknown                     /proc/sys/net/ipv6/conf absent.

stdlib only.
"""
from __future__ import annotations

import os
from typing import Dict, List, Optional


NAME = "ipv6_conf_per_iface_audit"


_PROC_IPV6_CONF = "/proc/sys/net/ipv6/conf"


_KNOBS = (
    "accept_ra", "autoconf", "forwarding", "disable_ipv6",
    "accept_redirects", "use_tempaddr", "addr_gen_mode",
    "router_solicitations", "accept_source_route",
)


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


def list_interfaces(sys_conf: str = _PROC_IPV6_CONF
                         ) -> List[str]:
    if not os.path.isdir(sys_conf):
        return []
    try:
        return sorted(n for n in os.listdir(sys_conf)
                          if os.path.isdir(
                              os.path.join(sys_conf, n)))
    except OSError:
        return []


def read_iface_knobs(sys_conf: str, iface: str
                          ) -> Dict[str, Optional[int]]:
    d = os.path.join(sys_conf, iface)
    return {k: _read_int(os.path.join(d, k)) for k in _KNOBS}


def classify(present: bool,
              ifaces: Dict[str, Dict[str, Optional[int]]]
              ) -> dict:
    if not present:
        return {"verdict": "unknown",
                "reason": ("/proc/sys/net/ipv6/conf absent — "
                          "kernel built without IPv6."),
                "recommendation": ""}

    # 1) ra_accept_on_router — accept_ra=1 AND forwarding=1
    bad_ra = [n for n, k in ifaces.items()
                  if k.get("accept_ra") == 1
                    and k.get("forwarding") == 1]
    if bad_ra:
        sample = ", ".join(bad_ra[:3])
        return {"verdict": "ra_accept_on_router",
                "reason": (f"{len(bad_ra)} iface(s) accept "
                          f"Router Advertisements while "
                          f"forwarding : {sample}. RFC 4861 "
                          f"forbids this combination."),
                "recommendation": _recipe_ra_router()}

    # 2) tempaddr_disabled_public — non-lo iface use_tempaddr=0
    notemp = [n for n, k in ifaces.items()
                  if k.get("use_tempaddr") == 0
                    and n not in ("lo", "all", "default")]
    if notemp:
        sample = ", ".join(notemp[:3])
        return {"verdict": "tempaddr_disabled_public",
                "reason": (f"{len(notemp)} iface(s) with "
                          f"use_tempaddr=0 : {sample}. "
                          f"Stable EUI-64 leak across networks."),
                "recommendation": _recipe_tempaddr()}

    # 3) unsolicited_forwarding
    all_fwd = ifaces.get("all", {}).get("forwarding")
    if all_fwd == 1:
        return {"verdict": "unsolicited_forwarding",
                "reason": ("all/forwarding=1 — host is an IPv6 "
                          "router. Confirm intended (libvirt / "
                          "docker bridge / podman / Wireguard "
                          "endpoint)."),
                "recommendation": _recipe_forwarding()}

    # 4) redirects_accepted
    redir = [n for n, k in ifaces.items()
                if k.get("accept_redirects") == 1
                  and n not in ("lo", "all", "default")]
    if redir:
        sample = ", ".join(redir[:3])
        return {"verdict": "redirects_accepted",
                "reason": (f"{len(redir)} iface(s) accept "
                          f"ICMPv6 redirects : {sample}. "
                          f"Local re-route attack surface."),
                "recommendation": _recipe_redirects()}

    return {"verdict": "ok",
            "reason": (f"{len(ifaces)} iface(s) with sane "
                      f"defaults : no RA-on-router, tempaddr "
                      f"enabled, no unsolicited forwarding, "
                      f"no redirect-accept."),
            "recommendation": ""}


def status(config=None,
            sys_conf: str = _PROC_IPV6_CONF) -> dict:
    present = os.path.isdir(sys_conf)
    ifaces: Dict[str, Dict[str, Optional[int]]] = {}
    if present:
        for n in list_interfaces(sys_conf):
            ifaces[n] = read_iface_knobs(sys_conf, n)
    verdict = classify(present, ifaces)
    return {"ok": present,
              "present": present,
              "iface_count": len(ifaces),
              "ifaces": list(ifaces.keys()),
              "knobs": ifaces,
              "verdict": verdict}


# ── recovery recipes ────────────────────────────────────────────

def _recipe_ra_router() -> str:
    return ("# Routers MUST NOT accept RAs (RFC 4861). Disable :\n"
            "for d in /proc/sys/net/ipv6/conf/*/accept_ra; do\n"
            "  echo 0 | sudo tee \"$d\"\n"
            "done\n"
            "echo 'net.ipv6.conf.all.accept_ra = 0' \\\n"
            "  | sudo tee /etc/sysctl.d/99-ipv6-router.conf\n")


def _recipe_tempaddr() -> str:
    return ("# Enable RFC 4941 temporary addresses (privacy) :\n"
            "for d in /proc/sys/net/ipv6/conf/*/use_tempaddr; do\n"
            "  echo 2 | sudo tee \"$d\"\n"
            "done\n"
            "echo 'net.ipv6.conf.all.use_tempaddr = 2' \\\n"
            "  | sudo tee /etc/sysctl.d/99-ipv6-tempaddr.conf\n"
            "echo 'net.ipv6.conf.default.use_tempaddr = 2' \\\n"
            "  | sudo tee -a /etc/sysctl.d/99-ipv6-tempaddr.conf\n")


def _recipe_forwarding() -> str:
    return ("# IPv6 forwarding enabled. If this is intentional\n"
            "# (libvirt / docker / podman / Wireguard) ignore.\n"
            "# Otherwise :\n"
            "echo 0 | sudo tee /proc/sys/net/ipv6/conf/all/forwarding\n"
            "# Find what set it :\n"
            "grep -r 'forwarding' /etc/sysctl.d/ /etc/sysctl.conf "
            "2>/dev/null\n")


def _recipe_redirects() -> str:
    return ("# Disable accept_redirects on non-router hosts :\n"
            "for d in /proc/sys/net/ipv6/conf/*/accept_redirects; do\n"
            "  echo 0 | sudo tee \"$d\"\n"
            "done\n"
            "echo 'net.ipv6.conf.all.accept_redirects = 0' \\\n"
            "  | sudo tee /etc/sysctl.d/99-ipv6-redirects.conf\n")
