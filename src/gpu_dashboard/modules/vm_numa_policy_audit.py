"""Module vm_numa_policy_audit — vm.numa_stat + zonelist
order (R&D #107.1, weaker pick).

Two NUMA sysctls that no existing module reads (numa_topology
covers only the on/off balancing toggle):

  /proc/sys/vm/numa_stat
    1 = per-node /proc/zoneinfo + /sys/devices/system/node/*/numastat
        counters live. 0 disables them — kills observability.
  /proc/sys/vm/numa_zonelist_order
    Default / Node / Zone. 'Node' on multi-node setups defeats
    zone-fallback, hurts ZONE_NORMAL pressure.

Acknowledged weak: zonelist_order has been mostly a no-op since
kernel 4.x — kernel auto-picks. Fires rarely.

Reads :

  /proc/sys/vm/numa_stat
  /proc/sys/vm/numa_zonelist_order
  /sys/devices/system/node/online   (single vs multi-node)

Verdicts (worst-first) :

  numa_stat_disabled         warn    numa_stat=0 on multi-node —
                                     kills per-node counters,
                                     breaks PSI observability.
  legacy_zonelist_node       accent  zonelist_order='Node' on
                                     multi-node — defeats zone
                                     fallback.
  ok                                 single-node or sane.
  requires_root                      sysctls unreadable.
  unknown                            /proc/sys/vm absent.

stdlib only.
"""
from __future__ import annotations

import os
from typing import Optional

NAME = "vm_numa_policy_audit"

DEFAULT_VM = "/proc/sys/vm"
DEFAULT_NODE_ONLINE = "/sys/devices/system/node/online"


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


def _read_str(path: str) -> Optional[str]:
    t = _read_text(path)
    return t.strip() if t is not None else None


def is_multi_node(online_text: Optional[str]) -> bool:
    """Parse '0-1' or '0' or '0,2-3'."""
    if not online_text:
        return False
    s = online_text.strip()
    if "-" in s or "," in s:
        return True
    return False


def classify(vm_present: bool,
             numa_stat: Optional[int],
             zonelist_order: Optional[str],
             multi_node: bool) -> dict:
    if not vm_present:
        return {"verdict": "unknown",
                "reason": "/proc/sys/vm absent."}
    if numa_stat is None and zonelist_order is None:
        return {"verdict": "requires_root",
                "reason": (
                    "vm.numa_* unreadable — re-run as "
                    "root.")}
    if not multi_node:
        return {"verdict": "ok",
                "reason": (
                    "Single-node host — numa_stat / "
                    "zonelist_order have no practical "
                    "effect.")}

    # warn — numa_stat=0 on multi-node
    if numa_stat == 0:
        return {
            "verdict": "numa_stat_disabled",
            "reason": (
                "vm.numa_stat=0 on a multi-node host — "
                "per-node /proc/zoneinfo + numastat "
                "counters are dead. PSI / memory pressure "
                "telemetry blind.")}

    # accent — zonelist_order=Node on multi-node
    if zonelist_order and zonelist_order.lower() == "node":
        return {
            "verdict": "legacy_zonelist_node",
            "reason": (
                "vm.numa_zonelist_order='Node' on multi-node "
                "— defeats zone-fallback ; small per-node "
                "ZONE_NORMAL fills before falling back.")}

    return {"verdict": "ok",
            "reason": (
                f"numa_stat={numa_stat} ; "
                f"zonelist_order={zonelist_order}. Sane.")}


def status(config: Optional[dict] = None,
           vm: str = DEFAULT_VM,
           node_online: str = DEFAULT_NODE_ONLINE) -> dict:
    vm_present = os.path.isdir(vm)
    numa_stat = (
        _read_int(os.path.join(vm, "numa_stat"))
        if vm_present else None)
    zonelist_order = (
        _read_str(os.path.join(vm, "numa_zonelist_order"))
        if vm_present else None)
    online = _read_text(node_online)
    multi_node = is_multi_node(online)
    verdict = classify(vm_present, numa_stat,
                       zonelist_order, multi_node)
    return {
        "ok": verdict["verdict"] == "ok",
        "numa_stat": numa_stat,
        "numa_zonelist_order": zonelist_order,
        "multi_node": multi_node,
        "verdict": verdict,
    }
