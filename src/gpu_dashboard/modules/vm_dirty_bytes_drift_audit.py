"""Module vm_dirty_bytes_drift_audit — dirty_bytes vs ratio
silent override (R&D #107.3).

Linux exposes dirty-page writeback thresholds in two parallel
forms:

  vm.dirty_background_ratio  / vm.dirty_background_bytes
  vm.dirty_ratio             / vm.dirty_bytes

The kernel uses *whichever is non-zero*, with bytes silently
winning when both are set. Real-world trap: a tuning script
sets dirty_bytes=2GB hours ago, sysctl tools show dirty_ratio=
10% still, and operators think 10 % is the active limit. It
isn't.

Existing vm_sysctl_audit + vm_tuning_deep read only the *ratio*
form. Neither touches the bytes pair (grep-confirmed: zero
hits).

Reads :

  /proc/sys/vm/dirty_bytes
  /proc/sys/vm/dirty_background_bytes
  /proc/sys/vm/dirty_ratio                  (cross-check)
  /proc/sys/vm/dirty_background_ratio

Verdicts (worst-first) :

  dirty_bytes_overrides_ratio       warn   dirty_bytes > 0 —
                                           dirty_ratio is dead.
                                           Operators see ratio
                                           value but bytes wins.
  dirty_bg_bytes_overrides_ratio    warn   dirty_background_bytes
                                           > 0 — same trap for
                                           background writeback.
  ok                                       both bytes = 0 (ratio
                                           form active).
  requires_root                            sysctls unreadable.
  unknown                                  /proc/sys/vm absent.

stdlib only.
"""
from __future__ import annotations

import os
from typing import Optional

NAME = "vm_dirty_bytes_drift_audit"

DEFAULT_VM = "/proc/sys/vm"


def _read_int(path: str) -> Optional[int]:
    try:
        with open(path, "r", encoding="utf-8") as fh:
            return int(fh.read().strip())
    except (OSError, PermissionError, ValueError):
        return None


def classify(vm_present: bool,
             dirty_bytes: Optional[int],
             dirty_bg_bytes: Optional[int],
             dirty_ratio: Optional[int],
             dirty_bg_ratio: Optional[int]) -> dict:
    if not vm_present:
        return {"verdict": "unknown",
                "reason": "/proc/sys/vm absent."}
    if (dirty_bytes is None and dirty_bg_bytes is None
            and dirty_ratio is None
            and dirty_bg_ratio is None):
        return {"verdict": "requires_root",
                "reason": (
                    "vm.dirty_* sysctls unreadable — "
                    "re-run as root.")}

    if dirty_bytes is not None and dirty_bytes > 0:
        return {
            "verdict": "dirty_bytes_overrides_ratio",
            "reason": (
                f"vm.dirty_bytes={dirty_bytes} — "
                "dirty_ratio is silently ignored. "
                "Operators reading the ratio see "
                f"{dirty_ratio} % but bytes value "
                "wins.")}

    if dirty_bg_bytes is not None and dirty_bg_bytes > 0:
        return {
            "verdict": "dirty_bg_bytes_overrides_ratio",
            "reason": (
                f"vm.dirty_background_bytes={dirty_bg_bytes}"
                " — dirty_background_ratio is silently "
                f"ignored. Reading shows {dirty_bg_ratio} %"
                " but bytes value wins.")}

    return {"verdict": "ok",
            "reason": (
                f"dirty_bytes=0 ; dirty_bg_bytes=0 ; "
                f"ratio={dirty_ratio} % ; "
                f"bg_ratio={dirty_bg_ratio} %. Sane.")}


def status(config: Optional[dict] = None,
           vm: str = DEFAULT_VM) -> dict:
    vm_present = os.path.isdir(vm)
    dirty_bytes = (
        _read_int(os.path.join(vm, "dirty_bytes"))
        if vm_present else None)
    dirty_bg_bytes = (
        _read_int(os.path.join(
            vm, "dirty_background_bytes"))
        if vm_present else None)
    dirty_ratio = (
        _read_int(os.path.join(vm, "dirty_ratio"))
        if vm_present else None)
    dirty_bg_ratio = (
        _read_int(os.path.join(
            vm, "dirty_background_ratio"))
        if vm_present else None)
    verdict = classify(vm_present, dirty_bytes,
                       dirty_bg_bytes, dirty_ratio,
                       dirty_bg_ratio)
    return {
        "ok": verdict["verdict"] == "ok",
        "dirty_bytes": dirty_bytes,
        "dirty_background_bytes": dirty_bg_bytes,
        "dirty_ratio": dirty_ratio,
        "dirty_background_ratio": dirty_bg_ratio,
        "verdict": verdict,
    }
