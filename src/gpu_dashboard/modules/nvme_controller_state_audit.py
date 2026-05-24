"""Module nvme_controller_state_audit — NVMe controller
liveness + firmware-rev consistency (R&D #86.3).

Walks /sys/class/nvme/nvme<N>/ for the controller state
machine + identification.  Catches two homelab footguns
that don't reliably surface in dmesg :

  * a controller that has been sitting in state =
    ``resetting`` or ``connecting`` for hours (link
    flapping under load) ;
  * two identical Samsung 990 / WD SN850 drives running
    different firmware revisions — the classic "one drive
    is mysteriously slower" mystery.

Reads per /sys/class/nvme/nvme<N>/ :

  state          live | dead | resetting | connecting |
                 deleting | new
  cntrltype      io | discovery | admin
  firmware_rev   firmware version string
  numa_node      NUMA node (-1 if unset)
  transport      pcie | tcp | rdma | fc
  model          drive model string (used to detect
                 same-model firmware mismatch)

Verdicts (worst first) :

  controller_dead              state in {dead, deleting}
                               — drive failed or being
                               removed.
  controller_resetting         state in {resetting,
                               connecting} — flapping
                               link.
  firmware_mismatch_same_model ≥2 controllers with same
                               model but different
                               firmware_rev — silent perf
                               divergence.
  numa_node_unset              numa_node = -1 on x86 host
                               — scheduler can't pin IO
                               threads.
  ok                           all live, NUMA correct,
                               firmware consistent.
  unknown                      /sys/class/nvme absent or
                               empty (no NVMe storage).
"""
from __future__ import annotations

import os
import re
from typing import Optional

DEFAULT_NVME_ROOT = "/sys/class/nvme"

_BAD_STATES = {"dead", "deleting"}
_FLAPPING_STATES = {"resetting", "connecting"}

_NVME_NAME_RE = re.compile(r"^nvme\d+$")


def _read_text(path: str) -> Optional[str]:
    try:
        with open(path, "r", encoding="utf-8") as fh:
            return fh.read().strip()
    except (OSError, PermissionError):
        return None


def _read_int(path: str) -> Optional[int]:
    s = _read_text(path)
    if s is None or s == "":
        return None
    try:
        return int(s)
    except ValueError:
        return None


def list_controllers(root: str = DEFAULT_NVME_ROOT
                       ) -> list[str]:
    try:
        return sorted(
            n for n in os.listdir(root)
            if _NVME_NAME_RE.match(n))
    except OSError:
        return []


def read_controller(root: str, name: str) -> dict:
    d = os.path.join(root, name)
    return {
        "name": name,
        "state": _read_text(os.path.join(d, "state")) or "",
        "cntrltype": _read_text(
            os.path.join(d, "cntrltype")) or "",
        "firmware_rev": _read_text(
            os.path.join(d, "firmware_rev")) or "",
        "numa_node": _read_int(
            os.path.join(d, "numa_node")),
        "transport": _read_text(
            os.path.join(d, "transport")) or "",
        "model": _read_text(os.path.join(d, "model")) or "",
    }


def _detect_firmware_mismatch(
        controllers: list[dict]) -> Optional[dict]:
    """Returns the first model with >=2 firmware revs."""
    by_model: dict[str, set[str]] = {}
    for c in controllers:
        model = c.get("model") or ""
        fw = c.get("firmware_rev") or ""
        if not model or not fw:
            continue
        by_model.setdefault(model, set()).add(fw)
    for model, fws in by_model.items():
        if len(fws) >= 2:
            return {"model": model, "firmware_revs": sorted(fws)}
    return None


def classify(controllers: list[dict]) -> dict:
    if not controllers:
        return {"verdict": "unknown",
                "reason": (
                    "/sys/class/nvme absent or empty — no "
                    "NVMe controllers on this host.")}

    # 1. err — dead controller
    dead = [c for c in controllers
             if c["state"] in _BAD_STATES]
    if dead:
        first = dead[0]
        return {"verdict": "controller_dead",
                "reason": (
                    f"{first['name']} state = "
                    f"{first['state']} — controller failed "
                    "or under removal."),
                "controller": first["name"],
                "state": first["state"]}

    # 2. warn — resetting / connecting
    flap = [c for c in controllers
             if c["state"] in _FLAPPING_STATES]
    if flap:
        first = flap[0]
        return {"verdict": "controller_resetting",
                "reason": (
                    f"{first['name']} state = "
                    f"{first['state']} — flapping or "
                    "stuck during link bring-up."),
                "controller": first["name"],
                "state": first["state"]}

    # 3. warn — firmware mismatch on same model
    mismatch = _detect_firmware_mismatch(controllers)
    if mismatch:
        return {"verdict": "firmware_mismatch_same_model",
                "reason": (
                    f"Model '{mismatch['model']}' has "
                    f"{len(mismatch['firmware_revs'])} "
                    "different firmware revisions across "
                    "controllers: "
                    f"{','.join(mismatch['firmware_revs'])}."),
                "model": mismatch["model"],
                "firmware_revs": mismatch["firmware_revs"]}

    # 4. accent — numa_node = -1 on x86
    unset_numa = [
        c for c in controllers
        if c.get("numa_node") == -1
        and c.get("transport") == "pcie"]
    if unset_numa:
        return {"verdict": "numa_node_unset",
                "reason": (
                    f"{len(unset_numa)} PCIe controller(s) "
                    "have numa_node = -1 — kernel scheduler "
                    "can't pin IO threads to a local node."),
                "count": len(unset_numa),
                "controllers": [c["name"]
                                  for c in unset_numa]}

    return {"verdict": "ok",
            "reason": (
                f"{len(controllers)} NVMe controller(s) live "
                "; firmware consistent ; NUMA topology "
                "correct.")}


def status(config: Optional[dict] = None,
           root: str = DEFAULT_NVME_ROOT) -> dict:
    controllers = [
        read_controller(root, n)
        for n in list_controllers(root)]
    verdict = classify(controllers)
    return {
        "ok": verdict["verdict"] not in (
            "controller_dead", "unknown"),
        "controller_count": len(controllers),
        "controllers": controllers,
        "verdict": verdict,
    }
