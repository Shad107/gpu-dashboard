"""Module dm_mod_params_audit — device-mapper driver-global
parameters (R&D #108.3, weaker pick).

The kernel exposes a handful of dm_mod tunables that govern
request-queue mode, NUMA pinning and per-IO reservations.
None of them are tracked by kernel_module_params_drift_audit
(which lists usbcore / zswap / ksm / nvme / xhci_hcd / i915 /
amdgpu only). block_holders_stack_audit + block_queue_audit
walk /sys/block/dm-*, not driver-global knobs.

Reads :

  /sys/module/dm_mod/parameters/use_blk_mq
    Y = blk-mq (modern). N = legacy request-based (slower).
  /sys/module/dm_mod/parameters/dm_numa_node
    -1 = any (default). Non-default pin can starve hot pages.
  /sys/module/dm_mod/parameters/reserved_bio_based_ios
  /sys/module/dm_mod/parameters/reserved_rq_based_ios

Acknowledged weak pick (R&D #108 survey honesty): mostly fires
'unknown' on hosts without dm/LVM/LUKS workloads.

Verdicts (worst-first) :

  dm_use_blk_mq_off       accent   use_blk_mq=N — request-based
                                   legacy mode, slower path.
  dm_numa_node_pinned     accent   dm_numa_node != -1 — pinned
                                   to a specific node ; can
                                   starve hot pages on multi-
                                   socket.
  ok                               defaults intact.
  requires_root                    params unreadable.
  unknown                          dm_mod not loaded.

stdlib only.
"""
from __future__ import annotations

import os
from typing import Optional

NAME = "dm_mod_params_audit"

DEFAULT_SYSFS = "/sys/module/dm_mod/parameters"


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


def _read_bool_param(path: str) -> Optional[bool]:
    t = _read_text(path)
    if t is None:
        return None
    s = t.strip().upper()
    if s in ("Y", "1", "TRUE"):
        return True
    if s in ("N", "0", "FALSE"):
        return False
    return None


def classify(module_present: bool,
             use_blk_mq: Optional[bool],
             dm_numa_node: Optional[int]) -> dict:
    if not module_present:
        return {"verdict": "unknown",
                "reason": (
                    "/sys/module/dm_mod absent — no dm/LVM/"
                    "LUKS workload on this host.")}
    if use_blk_mq is None and dm_numa_node is None:
        return {"verdict": "requires_root",
                "reason": (
                    "dm_mod params unreadable — re-run "
                    "as root.")}

    if use_blk_mq is False:
        return {
            "verdict": "dm_use_blk_mq_off",
            "reason": (
                "dm_mod.use_blk_mq=N — request-based legacy "
                "mode, slower than blk-mq. Bump via "
                "`echo Y | tee /sys/module/dm_mod/parameters/use_blk_mq` "
                "(or modprobe option).")}

    if dm_numa_node is not None and dm_numa_node != -1:
        return {
            "verdict": "dm_numa_node_pinned",
            "reason": (
                f"dm_mod.dm_numa_node={dm_numa_node} "
                "(not -1) — dm threads pinned to a "
                "specific node ; can starve hot pages on "
                "multi-socket hosts.")}

    return {"verdict": "ok",
            "reason": (
                f"use_blk_mq={use_blk_mq} ; "
                f"dm_numa_node={dm_numa_node}. Sane.")}


def status(config: Optional[dict] = None,
           sysfs: str = DEFAULT_SYSFS) -> dict:
    module_present = os.path.isdir(sysfs)
    use_blk_mq = (
        _read_bool_param(os.path.join(sysfs, "use_blk_mq"))
        if module_present else None)
    dm_numa_node = (
        _read_int(os.path.join(sysfs, "dm_numa_node"))
        if module_present else None)
    verdict = classify(module_present, use_blk_mq,
                       dm_numa_node)
    return {
        "ok": verdict["verdict"] == "ok",
        "use_blk_mq": use_blk_mq,
        "dm_numa_node": dm_numa_node,
        "verdict": verdict,
    }
