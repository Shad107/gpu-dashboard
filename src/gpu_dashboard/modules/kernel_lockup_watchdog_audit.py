"""Module kernel_lockup_watchdog_audit — soft/hard lockup
detector posture (R&D #92.2).

Two existing modules touch related surface :

  * panic_policy — reports softlockup/hardlockup *panic*
    knobs as part of crash policy bundle ; never reads the
    detector enable flags or watchdog_thresh.
  * watchdog_inventory — walks /dev/watchdog* HW devices
    (different concept — physical reset watchdogs).

Neither reads the kernel's soft/hard lockup detector
parameters. This audit owns that gap.

Reads :

  /proc/sys/kernel/watchdog           soft+hard global toggle
  /proc/sys/kernel/nmi_watchdog       hard lockup toggle
  /proc/sys/kernel/watchdog_thresh    soft-lockup threshold (s)
  /proc/sys/kernel/soft_watchdog      soft lockup toggle
  /sys/devices/system/cpu/nmi_watchdog HW support indicator

Verdicts (worst-first) :

  watchdog_fully_disabled   err   both watchdog=0 AND
                                  nmi_watchdog=0 — no lockup
                                  detector at all.
  nmi_watchdog_disabled     warn  watchdog=1 but
                                  nmi_watchdog=0 AND the
                                  hardware supports it
                                  (/sys/devices/system/cpu/
                                  nmi_watchdog present).
  watchdog_thresh_high      accent watchdog_thresh > 30 s —
                                  soft-lockup detection so
                                  loose that GPU/IO hangs
                                  could go silent for minutes.
  ok                        sane.
  unknown                   /proc/sys/kernel/watchdog absent
                            (kernel built without
                            LOCKUP_DETECTOR).

stdlib only.
"""
from __future__ import annotations

import os
from typing import Optional

NAME = "kernel_lockup_watchdog_audit"

DEFAULT_PROC_SYS_KERNEL = "/proc/sys/kernel"
DEFAULT_SYS_CPU = "/sys/devices/system/cpu"

# Threshold for watchdog_thresh "too loose" (default = 10 s).
_THRESH_HIGH_S = 30


def _read_int(path: str) -> Optional[int]:
    try:
        with open(path, "r", encoding="utf-8") as fh:
            return int(fh.read().strip())
    except (OSError, PermissionError, ValueError):
        return None


def read_watchdog_state(
        proc_sys_kernel: str = DEFAULT_PROC_SYS_KERNEL,
        sys_cpu: str = DEFAULT_SYS_CPU) -> dict:
    return {
        "watchdog": _read_int(
            os.path.join(proc_sys_kernel, "watchdog")),
        "nmi_watchdog": _read_int(
            os.path.join(proc_sys_kernel, "nmi_watchdog")),
        "watchdog_thresh": _read_int(
            os.path.join(
                proc_sys_kernel, "watchdog_thresh")),
        "soft_watchdog": _read_int(
            os.path.join(proc_sys_kernel, "soft_watchdog")),
        "nmi_hw_supported": os.path.exists(
            os.path.join(sys_cpu, "nmi_watchdog")),
    }


def classify(s: dict) -> dict:
    if s["watchdog"] is None and s["nmi_watchdog"] is None:
        return {"verdict": "unknown",
                "reason": (
                    "/proc/sys/kernel/watchdog absent — "
                    "kernel built without "
                    "CONFIG_LOCKUP_DETECTOR or procfs "
                    "unavailable.")}

    # err — both detectors off
    if (s["watchdog"] == 0 and s["nmi_watchdog"] == 0):
        return {
            "verdict": "watchdog_fully_disabled",
            "reason": (
                "Both watchdog and nmi_watchdog are 0 — no "
                "lockup detector active. GPU hangs and CPU "
                "soft-locks will produce zero kernel trace.")}

    # warn — NMI off where HW supports it
    if (s["watchdog"] == 1
            and s["nmi_watchdog"] == 0
            and s["nmi_hw_supported"]):
        return {
            "verdict": "nmi_watchdog_disabled",
            "reason": (
                "nmi_watchdog = 0 on a system that exposes "
                "/sys/devices/system/cpu/nmi_watchdog — "
                "hardlockup detection is OFF. RTX 3090 GPU "
                "hangs that wedge IRQs go silent.")}

    # accent — threshold so loose detection is meaningless
    thresh = s["watchdog_thresh"]
    if thresh is not None and thresh > _THRESH_HIGH_S:
        return {
            "verdict": "watchdog_thresh_high",
            "reason": (
                f"watchdog_thresh = {thresh} s (> "
                f"{_THRESH_HIGH_S}) — soft-lockup detector "
                "won't trigger until a stall has lasted that "
                "long. Tighten to 10 s for desktop / 60 s "
                "for batch workloads."),
            "thresh": thresh}

    return {"verdict": "ok",
            "reason": (
                f"watchdog={s['watchdog']}, "
                f"nmi_watchdog={s['nmi_watchdog']} "
                f"(HW supported: {s['nmi_hw_supported']}), "
                f"thresh={thresh}s — coherent.")}


def status(config: Optional[dict] = None,
           proc_sys_kernel: str = DEFAULT_PROC_SYS_KERNEL,
           sys_cpu: str = DEFAULT_SYS_CPU) -> dict:
    state = read_watchdog_state(proc_sys_kernel, sys_cpu)
    verdict = classify(state)
    return {
        "ok": verdict["verdict"] == "ok",
        "watchdog": state["watchdog"],
        "nmi_watchdog": state["nmi_watchdog"],
        "watchdog_thresh": state["watchdog_thresh"],
        "nmi_hw_supported": state["nmi_hw_supported"],
        "verdict": verdict,
    }
