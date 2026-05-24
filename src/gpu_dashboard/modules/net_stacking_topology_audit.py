"""Module net_stacking_topology_audit — bond / bridge /
team / lower-upper sanity check (R&D #78.2).

Walks /sys/class/net/<iface>/ looking for :

* bond masters     (``bonding/`` subdir, ``bonding/slaves``)
* bridge masters   (``bridge/`` subdir, ``brif/`` port list)
* slave-of-master  (``master`` symlink)
* upper / lower    (``lower_<name>``, ``upper_<name>`` symlinks)

The point of this audit is to flag *drift* in netdev stacking
that has already broken redundancy or symmetry — situations
where a NetworkManager reload, a flaky NIC, or a botched
bond/bridge teardown left the kernel in a half-state.

Verdicts (worst first) :

* bond_degraded_slave  — a bond exists but one of its slave
                         interfaces is not ``up``, or the
                         bond has no slaves at all.
* bridge_stp_disabled  — a non-Docker bridge has ≥2 ports
                         and STP is off (loop risk).
                         Docker bridges (docker0, br-<hex>)
                         are filtered out — STP is
                         intentionally disabled there.
* orphan_lower_member  — an iface has ``master`` link to a
                         master that no longer exists.
* stacking_inconsistent — lower_X / upper_X asymmetry
                          (upper claims X is below it but X
                          does not have upper symlink back).
* ok                    — no stacking, or stacking healthy.
* unknown               — /sys/class/net missing.
"""
from __future__ import annotations

import os
import re
from typing import Optional

DEFAULT_NET_ROOT = "/sys/class/net"

# docker0, docker_gwbridge, br-<10+ hex chars> (compose default)
DOCKER_BRIDGE_RE = re.compile(r"^(docker[\w]*|br-[0-9a-f]{10,})$")


def _read_text(path: str) -> Optional[str]:
    try:
        with open(path, "r", encoding="utf-8") as fh:
            return fh.read().strip()
    except (OSError, PermissionError):
        return None


def _read_int(path: str) -> Optional[int]:
    s = _read_text(path)
    if s is None:
        return None
    try:
        return int(s)
    except ValueError:
        return None


def _link_basename(path: str) -> Optional[str]:
    try:
        return os.path.basename(os.readlink(path))
    except OSError:
        return None


def list_interfaces(root: str = DEFAULT_NET_ROOT) -> list[str]:
    try:
        return sorted(os.listdir(root))
    except OSError:
        return []


def inspect_iface(root: str, name: str) -> dict:
    d = os.path.join(root, name)
    info: dict = {
        "name": name,
        "operstate": _read_text(os.path.join(d, "operstate")),
        "master": _link_basename(os.path.join(d, "master")),
        "is_bond": os.path.isdir(os.path.join(d, "bonding")),
        "is_bridge": os.path.isdir(os.path.join(d, "bridge")),
        "lowers": [],
        "uppers": [],
        "ports": [],
        "slaves": [],
        "bonding": {},
        "bridge": {},
    }
    try:
        for entry in os.listdir(d):
            if entry.startswith("lower_"):
                info["lowers"].append(entry[len("lower_"):])
            elif entry.startswith("upper_"):
                info["uppers"].append(entry[len("upper_"):])
    except OSError:
        pass

    if info["is_bond"]:
        info["bonding"]["mode"] = _read_text(
            os.path.join(d, "bonding", "mode"))
        slaves_raw = _read_text(
            os.path.join(d, "bonding", "slaves")) or ""
        info["slaves"] = slaves_raw.split() if slaves_raw else []
        info["bonding"]["active_slave"] = _read_text(
            os.path.join(d, "bonding", "active_slave"))
        info["bonding"]["mii_status"] = _read_text(
            os.path.join(d, "bonding", "mii_status"))

    if info["is_bridge"]:
        info["bridge"]["stp_state"] = _read_int(
            os.path.join(d, "bridge", "stp_state"))
        brif = os.path.join(d, "brif")
        try:
            info["ports"] = sorted(os.listdir(brif))
        except OSError:
            info["ports"] = []
    return info


def classify(net_present: bool, ifaces: list[dict]) -> dict:
    if not net_present or not ifaces:
        return {"verdict": "unknown",
                "reason": "/sys/class/net unreadable or empty."}

    by_name = {i["name"]: i for i in ifaces}

    # 1. bond_degraded_slave
    for i in ifaces:
        if not i["is_bond"]:
            continue
        slaves = i["slaves"]
        if not slaves:
            return {"verdict": "bond_degraded_slave",
                    "reason": f"Bond {i['name']} has no slaves attached.",
                    "bond": i["name"], "bad_slaves": []}
        bad = []
        for s in slaves:
            s_info = by_name.get(s)
            if s_info is None:
                bad.append(s)
                continue
            if s_info.get("operstate") and s_info["operstate"] != "up":
                bad.append(s)
        if bad:
            return {"verdict": "bond_degraded_slave",
                    "reason": f"Bond {i['name']} has degraded slave(s): "
                              f"{','.join(bad)}.",
                    "bond": i["name"], "bad_slaves": bad}

    # 2. bridge_stp_disabled — non-Docker bridges with ≥2 ports + stp off
    for i in ifaces:
        if not i["is_bridge"]:
            continue
        if DOCKER_BRIDGE_RE.match(i["name"]):
            continue
        ports = i["ports"]
        if len(ports) < 2:
            continue
        if i["bridge"].get("stp_state") == 0:
            return {"verdict": "bridge_stp_disabled",
                    "reason": f"Bridge {i['name']} has {len(ports)} "
                              "ports and STP disabled.",
                    "bridge": i["name"], "port_count": len(ports)}

    # 3. orphan_lower_member
    for i in ifaces:
        m = i.get("master")
        if m and m not in by_name:
            return {"verdict": "orphan_lower_member",
                    "reason": f"Interface {i['name']} references "
                              f"missing master {m}.",
                    "orphan": i["name"], "missing_master": m}

    # 4. stacking_inconsistent — lower/upper asymmetry
    for i in ifaces:
        for lower in i["lowers"]:
            lower_info = by_name.get(lower)
            if lower_info is None:
                return {"verdict": "stacking_inconsistent",
                        "reason": f"{i['name']} lists lower {lower} "
                                  "but that iface is missing.",
                        "iface": i["name"], "missing_lower": lower}
            if i["name"] not in lower_info["uppers"]:
                return {"verdict": "stacking_inconsistent",
                        "reason": f"{i['name']} lists {lower} as "
                                  f"lower but {lower}.upper does not "
                                  f"include {i['name']}.",
                        "iface": i["name"], "lower": lower}

    return {"verdict": "ok",
            "reason": f"{len(ifaces)} interface(s) audited ; "
                      "no stacking drift."}


def status(config: Optional[dict] = None,
           net_root: str = DEFAULT_NET_ROOT) -> dict:
    present = os.path.isdir(net_root)
    names = list_interfaces(net_root) if present else []
    ifaces = [inspect_iface(net_root, n) for n in names]
    verdict = classify(present and bool(ifaces), ifaces)
    return {
        "ok": verdict["verdict"] != "unknown",
        "iface_count": len(ifaces),
        "bonds": [i["name"] for i in ifaces if i["is_bond"]],
        "bridges": [i["name"] for i in ifaces if i["is_bridge"]],
        "interfaces": ifaces,
        "verdict": verdict,
    }
