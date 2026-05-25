"""Module numa_demotion_enabled_audit — NUMA demotion boolean
posture (R&D #109.1, weakest pick).

Kernel 5.15+ exposes a single boolean controlling whether cold
pages can be demoted from a hot DRAM node to a slower NUMA tier
(CXL.mem, PMEM in non-app-direct mode, far DDR):

  /sys/kernel/mm/numa/demotion_enabled

numa_topology_audit only mentions this path in its docstring —
grep confirms zero actual reads.

Weakest pick — single boolean ; only warns on multi-node hosts
with cold-tier memory. On single-socket homelabs the verdict is
informational.

Reads :

  /sys/kernel/mm/numa/demotion_enabled
  /sys/devices/system/node/online

Verdicts (worst-first) :

  demotion_off_with_tiered_memory   warn   demotion_enabled=false
                                           AND > 1 NUMA node —
                                           cold pages will not
                                           drift to slower tier.
  demotion_on_single_node           accent demotion=true on single
                                           node — config has no
                                           effect, signals copied
                                           cluster-tuning.
  ok                                       single-node (info) or
                                           demotion on + multi-
                                           node.
  requires_root                            sysfs unreadable.
  unknown                                  demotion_enabled
                                           sysfs absent (kernel
                                           < 5.15).

stdlib only.
"""
from __future__ import annotations

import os
from typing import Optional

NAME = "numa_demotion_enabled_audit"

DEFAULT_DEMOTION = "/sys/kernel/mm/numa/demotion_enabled"
DEFAULT_NODE_ONLINE = "/sys/devices/system/node/online"


def _read_text(path: str) -> Optional[str]:
    try:
        with open(path, "r", encoding="utf-8") as fh:
            return fh.read()
    except (OSError, PermissionError):
        return None


def _parse_bool(text: Optional[str]) -> Optional[bool]:
    if text is None:
        return None
    s = text.strip().lower()
    if s in ("1", "true", "y"):
        return True
    if s in ("0", "false", "n"):
        return False
    return None


def is_multi_node(online_text: Optional[str]) -> bool:
    if not online_text:
        return False
    s = online_text.strip()
    return "-" in s or "," in s


def classify(present: bool,
             demotion: Optional[bool],
             multi_node: bool) -> dict:
    if not present:
        return {"verdict": "unknown",
                "reason": (
                    "/sys/kernel/mm/numa/demotion_enabled "
                    "absent — kernel < 5.15.")}
    if demotion is None:
        return {"verdict": "requires_root",
                "reason": (
                    "demotion_enabled unreadable — re-run "
                    "as root.")}

    if multi_node and demotion is False:
        return {
            "verdict": "demotion_off_with_tiered_memory",
            "reason": (
                "demotion_enabled=false on multi-node host "
                "— cold pages stay pinned to hot DRAM. "
                "Enable for CXL.mem / PMEM tiering benefit.")}

    if not multi_node and demotion is True:
        return {
            "verdict": "demotion_on_single_node",
            "reason": (
                "demotion_enabled=true on single-node host "
                "— config has no effect ; signals "
                "tuning script copied from a cluster.")}

    return {"verdict": "ok",
            "reason": (
                f"demotion_enabled={demotion} ; "
                f"multi_node={multi_node}. Coherent.")}


def status(config: Optional[dict] = None,
           demotion_path: str = DEFAULT_DEMOTION,
           node_online: str = DEFAULT_NODE_ONLINE) -> dict:
    present = os.path.isfile(demotion_path)
    demotion = _parse_bool(_read_text(demotion_path))
    multi = is_multi_node(_read_text(node_online))
    verdict = classify(present, demotion, multi)
    return {
        "ok": verdict["verdict"] == "ok",
        "demotion_enabled": demotion,
        "multi_node": multi,
        "verdict": verdict,
    }
