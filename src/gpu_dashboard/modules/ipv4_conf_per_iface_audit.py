"""Module ipv4_conf_per_iface_audit — per-interface IPv4
networking knobs audit (R&D #75.4).

Existing net_sysctl_audit covers global /proc/sys/net/core
tunables. This audit drills into the per-interface knobs under
/proc/sys/net/ipv4/conf/<iface>/ which are independently
adjustable (and easy to leave at insecure defaults on a
desktop/homelab).

Reads (per iface + all/ + default/) :
  rp_filter              0 = no source validation
                          1 = strict (RFC 3704)
                          2 = loose
  accept_redirects       1 = accept ICMP-redirect (re-routes)
  accept_source_route    1 = accept LSRR / SSRR (CVE-2020-* level)
  send_redirects         1 = send ICMP-redirect on multi-hop
  log_martians           0 = no log on bogus packets
  arp_ignore             ARP reply policy
  arp_announce           ARP source-IP advertisement policy
  forwarding             1 = act as router

Why on a homelab :

* `accept_source_route=1` is a textbook info-leak / spoof vector
  routinely missed on default-installed distros.
* `rp_filter=0` on a forwarding host = SYN-flood amplification
  surface ; loose (=2) is fine.
* `forwarding=1` is expected on a docker / libvirt host but
  unusual on a plain desktop ; surfacing it informs the user.

Verdicts (priority order) :
  source_route_accepted     ≥1 iface accept_source_route == 1.
  rp_filter_loose           ≥1 iface rp_filter == 0
                              (no source validation at all ;
                              loose=2 is fine).
  redirects_accepted        ≥1 iface accept_redirects == 1
                              (excluding lo).
  forwarding_unexpected     all/forwarding == 1 (host is a
                              router — confirm intended).
  ok                        defaults look sane.
  unknown                   /proc/sys/net/ipv4/conf absent.

stdlib only.
"""
from __future__ import annotations

import os
from typing import Dict, List, Optional


NAME = "ipv4_conf_per_iface_audit"


_PROC_IPV4_CONF = "/proc/sys/net/ipv4/conf"


_KNOBS = (
    "rp_filter", "accept_redirects", "accept_source_route",
    "send_redirects", "log_martians", "arp_ignore",
    "arp_announce", "forwarding",
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


def list_interfaces(sys_conf: str = _PROC_IPV4_CONF
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
    out: Dict[str, Optional[int]] = {}
    d = os.path.join(sys_conf, iface)
    for k in _KNOBS:
        out[k] = _read_int(os.path.join(d, k))
    return out


def classify(present: bool,
              ifaces: Dict[str, Dict[str, Optional[int]]]) -> dict:
    if not present:
        return {"verdict": "unknown",
                "reason": ("/proc/sys/net/ipv4/conf absent — "
                          "no IPv4 net subsystem."),
                "recommendation": ""}

    # 1) source_route_accepted
    src = [n for n, k in ifaces.items()
              if (k.get("accept_source_route") or 0) == 1]
    if src:
        sample = ", ".join(src[:3])
        return {"verdict": "source_route_accepted",
                "reason": (f"{len(src)} iface(s) with "
                          f"accept_source_route=1 : {sample}. "
                          f"LSRR/SSRR options accepted (spoof "
                          f"vector)."),
                "recommendation": _recipe_src_route()}

    # 2) rp_filter_loose — strictly only flag 0 (no validation
    #    at all). Value 2 = loose RFC 3704 is fine.
    bad_rp = [n for n, k in ifaces.items()
                  if k.get("rp_filter") == 0
                    and n != "lo"]
    if bad_rp:
        sample = ", ".join(bad_rp[:3])
        return {"verdict": "rp_filter_loose",
                "reason": (f"{len(bad_rp)} iface(s) with "
                          f"rp_filter=0 : {sample}. No source "
                          f"validation; SYN-flood amplification "
                          f"surface."),
                "recommendation": _recipe_rp_filter()}

    # 3) redirects_accepted
    redir = [n for n, k in ifaces.items()
                if (k.get("accept_redirects") or 0) == 1
                  and n not in ("lo", "all", "default")]
    if redir:
        sample = ", ".join(redir[:3])
        return {"verdict": "redirects_accepted",
                "reason": (f"{len(redir)} iface(s) accept ICMP "
                          f"redirects : {sample}. Local re-route "
                          f"attack surface."),
                "recommendation": _recipe_redirects()}

    # 4) forwarding_unexpected — all/forwarding == 1
    all_fwd = ifaces.get("all", {}).get("forwarding")
    if all_fwd == 1:
        return {"verdict": "forwarding_unexpected",
                "reason": ("all/forwarding = 1 — host is acting "
                          "as an IPv4 router. Expected on "
                          "docker / libvirt hosts; unusual on a "
                          "plain desktop."),
                "recommendation": _recipe_forwarding()}

    return {"verdict": "ok",
            "reason": (f"{len(ifaces)} iface(s) with sane "
                      f"defaults : no source-route, no rp=0, "
                      f"no redirect-accept, forwarding off."),
            "recommendation": ""}


def status(config=None,
            sys_conf: str = _PROC_IPV4_CONF) -> dict:
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

def _recipe_src_route() -> str:
    return ("# accept_source_route is a textbook spoof vector.\n"
            "# Disable on every iface :\n"
            "for d in /proc/sys/net/ipv4/conf/*/"
            "accept_source_route; do\n"
            "  echo 0 | sudo tee \"$d\"\n"
            "done\n"
            "# Persist :\n"
            "echo 'net.ipv4.conf.all.accept_source_route = 0' \\\n"
            "  | sudo tee /etc/sysctl.d/99-no-source-route.conf\n"
            "echo 'net.ipv4.conf.default.accept_source_route = 0' \\\n"
            "  | sudo tee -a /etc/sysctl.d/99-no-source-route.conf\n")


def _recipe_rp_filter() -> str:
    return ("# rp_filter = 0 disables source validation.\n"
            "# Switch to strict (1) on end hosts, loose (2) on\n"
            "# multi-homed routers :\n"
            "for d in /proc/sys/net/ipv4/conf/*/rp_filter; do\n"
            "  echo 1 | sudo tee \"$d\"\n"
            "done\n"
            "echo 'net.ipv4.conf.all.rp_filter = 1' \\\n"
            "  | sudo tee /etc/sysctl.d/99-rp-filter.conf\n")


def _recipe_redirects() -> str:
    return ("# Disable accept_redirects on non-router hosts :\n"
            "for d in /proc/sys/net/ipv4/conf/*/"
            "accept_redirects; do\n"
            "  echo 0 | sudo tee \"$d\"\n"
            "done\n"
            "echo 'net.ipv4.conf.all.accept_redirects = 0' \\\n"
            "  | sudo tee /etc/sysctl.d/99-no-redirects.conf\n")


def _recipe_forwarding() -> str:
    return ("# Host has IPv4 forwarding enabled.\n"
            "# If this is intentional (docker / libvirt / WSL) :\n"
            "#   ignore — already correct.\n"
            "# If this is a plain desktop :\n"
            "echo 0 | sudo tee /proc/sys/net/ipv4/ip_forward\n"
            "# Find what set it :\n"
            "grep -r 'ip_forward\\|forwarding' /etc/sysctl.d/ "
            "/etc/sysctl.conf 2>/dev/null\n")
