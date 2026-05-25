"""Module umwait_control_audit — Intel umwait/tpause idle-hint
maximum-wait posture (R&D #99.1).

Intel UMWAIT/TPAUSE/UMONITOR (waitpkg feature) lets userspace
spin loops park the core in C0.1 or C0.2 instead of busy-
looping. C0.2 is deeper but loses cache state; C0.1 is shallow
and fast to wake.

  /sys/devices/system/cpu/umwait_control/enable_c02
    0 = userspace can only request C0.1
    1 = userspace can request C0.2 (kernel caps at max_time)
  /sys/devices/system/cpu/umwait_control/max_time
    Maximum cycles the kernel will park.  Default 100000.
    On a desktop, libuv / dpdk-style poll / llama.cpp busy
    loops / Wayland compositor wakeup-coalesce all hit
    UMWAIT — leaving max_time at 100000 with C0.2 enabled
    adds ~5-20 µs wake latency per fault, surfacing as
    input + audio stutter.

No existing module touches this surface (cpuidle_audit,
cpuidle_residency_audit, cpu_cppc_audit, hwp_epp, cpu_boost
target *kernel* idle governors).

Reads :

  /sys/devices/system/cpu/umwait_control/enable_c02
  /sys/devices/system/cpu/umwait_control/max_time
  /proc/cpuinfo                      (waitpkg feature flag)

Verdicts (worst-first) :

  umwait_c02_default_trap   err     waitpkg advertised AND
                                    enable_c02=1 AND
                                    max_time >= 100000 — the
                                    default trap on latency-
                                    sensitive desktops.
  umwait_c02_enabled        warn    enable_c02=1 with
                                    max_time < 100000.
  umwait_max_time_custom    accent  max_time tuned to non-
                                    default value, intent
                                    unclear.
  ok                                C0.2 disabled or feature
                                    absent.
  requires_root                     sysfs unreadable.
  unknown                           sysfs path absent.

stdlib only.
"""
from __future__ import annotations

import os
from typing import Optional

NAME = "umwait_control_audit"

DEFAULT_SYSFS_ROOT = "/sys/devices/system/cpu/umwait_control"
DEFAULT_CPUINFO = "/proc/cpuinfo"

_DEFAULT_MAX_TIME = 100000


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


def has_waitpkg(cpuinfo_text: Optional[str]) -> bool:
    """Return True if 'waitpkg' appears in /proc/cpuinfo flags."""
    if not cpuinfo_text:
        return False
    for line in cpuinfo_text.splitlines():
        if line.startswith("flags") or line.startswith("Features"):
            if "waitpkg" in line.split():
                return True
    return False


def classify(sysfs_present: bool,
             waitpkg: bool,
             enable_c02: Optional[int],
             max_time: Optional[int],
             unreadable: bool) -> dict:
    if not sysfs_present:
        return {"verdict": "unknown",
                "reason": (
                    "/sys/devices/system/cpu/umwait_control "
                    "absent — feature not exposed (non-Intel "
                    "or pre-Tremont/Snow Ridge CPU).")}
    if unreadable:
        return {"verdict": "requires_root",
                "reason": (
                    "umwait_control/* unreadable — re-run "
                    "as root.")}

    # If hardware doesn't advertise waitpkg, nothing to flag
    if not waitpkg:
        return {"verdict": "ok",
                "reason": (
                    "waitpkg flag absent in /proc/cpuinfo — "
                    "feature dormant.")}

    # err — default trap: C0.2 on + default max_time
    if (enable_c02 == 1
            and max_time is not None
            and max_time >= _DEFAULT_MAX_TIME):
        return {
            "verdict": "umwait_c02_default_trap",
            "reason": (
                f"enable_c02=1 AND max_time={max_time} "
                f"(>= default {_DEFAULT_MAX_TIME}). Userspace "
                "spin loops park the core in C0.2 and pay "
                "5-20 µs wake latency — audio / input "
                "stutter on a desktop.")}

    # warn — C0.2 enabled but max_time tightened
    if enable_c02 == 1:
        return {
            "verdict": "umwait_c02_enabled",
            "reason": (
                f"enable_c02=1 with max_time={max_time} "
                "(below default). C0.2 still costs cache "
                "state ; disable if latency matters.")}

    # accent — max_time deviates from default
    if (max_time is not None
            and max_time != _DEFAULT_MAX_TIME):
        return {
            "verdict": "umwait_max_time_custom",
            "reason": (
                f"max_time={max_time} (default "
                f"{_DEFAULT_MAX_TIME}). Custom value — "
                "intent unclear ; verify it matches "
                "workload profile.")}

    return {"verdict": "ok",
            "reason": (
                f"enable_c02={enable_c02} ; "
                f"max_time={max_time} — sane.")}


def status(config: Optional[dict] = None,
           sysfs_root: str = DEFAULT_SYSFS_ROOT,
           cpuinfo: str = DEFAULT_CPUINFO) -> dict:
    sysfs_present = os.path.isdir(sysfs_root)
    enable_c02 = (
        _read_int(os.path.join(sysfs_root, "enable_c02"))
        if sysfs_present else None)
    max_time = (
        _read_int(os.path.join(sysfs_root, "max_time"))
        if sysfs_present else None)
    unreadable = (
        sysfs_present
        and enable_c02 is None
        and max_time is None)

    waitpkg = has_waitpkg(_read_text(cpuinfo))

    verdict = classify(sysfs_present, waitpkg, enable_c02,
                       max_time, unreadable)
    return {
        "ok": verdict["verdict"] == "ok",
        "waitpkg": waitpkg,
        "enable_c02": enable_c02,
        "max_time": max_time,
        "verdict": verdict,
    }
