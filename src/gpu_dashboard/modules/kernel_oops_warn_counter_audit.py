"""Module kernel_oops_warn_counter_audit — /sys/kernel/oops_count
+ /sys/kernel/warn_count posture (R&D #103.1).

Kernel 6.2+ exposes two atomic counters that survive the dmesg
ring buffer:

  /sys/kernel/oops_count   total kernel oopses since boot
  /sys/kernel/warn_count   total WARN_ON()/pr_warn_ratelimit
                            counts since boot

These are the *cheapest possible* "did the kernel silently scream
overnight" signal — no dmesg scraping, no journald query, no root.
Existing modules (kernel_taint, kmsg_audit, panic_policy,
pstore_crashlog_audit, kernel_lockup_watchdog_audit) all read
dmesg / taint mask / pstore / hardlockup; none touch these new
counters.

Reads :

  /sys/kernel/oops_count
  /sys/kernel/warn_count
  /proc/sys/kernel/panic_on_oops  (cross-check)

Verdicts (worst-first) :

  silent_oops_since_boot   err     oops_count > 0 AND
                                   panic_on_oops=0 — kernel
                                   oopsed but kept running.
                                   State is suspect ; reboot.
  warn_count_high          warn    warn_count >= 5 — a driver
                                   / subsystem keeps firing
                                   WARN_ON().
  warn_count_nonzero       accent  warn_count in 1..4 — single
                                   surprise to investigate.
  ok                               both counters 0.
  requires_root                    files exist but unreadable.
  unknown                          counters absent (kernel <
                                   6.2).

stdlib only.
"""
from __future__ import annotations

import os
from typing import Optional

NAME = "kernel_oops_warn_counter_audit"

DEFAULT_OOPS = "/sys/kernel/oops_count"
DEFAULT_WARN = "/sys/kernel/warn_count"
DEFAULT_PANIC_ON_OOPS = "/proc/sys/kernel/panic_on_oops"

_WARN_HIGH_THRESHOLD = 5


def _read_int(path: str) -> Optional[int]:
    try:
        with open(path, "r", encoding="utf-8") as fh:
            return int(fh.read().strip())
    except (OSError, PermissionError, ValueError):
        return None


def classify(counters_present: bool,
             oops_count: Optional[int],
             warn_count: Optional[int],
             panic_on_oops: Optional[int]) -> dict:
    if not counters_present:
        return {"verdict": "unknown",
                "reason": (
                    "/sys/kernel/oops_count + warn_count "
                    "absent — kernel < 6.2.")}
    if oops_count is None and warn_count is None:
        return {"verdict": "requires_root",
                "reason": (
                    "counter files present but unreadable "
                    "— re-run as root.")}

    # err — silent oops survived
    if (oops_count is not None and oops_count > 0
            and (panic_on_oops is None
                 or panic_on_oops == 0)):
        return {
            "verdict": "silent_oops_since_boot",
            "reason": (
                f"oops_count={oops_count} AND "
                f"panic_on_oops={panic_on_oops} — kernel "
                "oopsed but kept running. State suspect ; "
                "reboot once safe and capture journal.")}

    # warn — many warnings
    if (warn_count is not None
            and warn_count >= _WARN_HIGH_THRESHOLD):
        return {
            "verdict": "warn_count_high",
            "reason": (
                f"warn_count={warn_count} (>= "
                f"{_WARN_HIGH_THRESHOLD}) — a driver / "
                "subsystem keeps firing WARN_ON(). "
                "Inspect dmesg.")}

    # accent — single-digit warnings
    if (warn_count is not None and 1 <= warn_count
            < _WARN_HIGH_THRESHOLD):
        return {
            "verdict": "warn_count_nonzero",
            "reason": (
                f"warn_count={warn_count} — at least one "
                "WARN_ON() fired since boot. Worth "
                "checking dmesg once.")}

    return {"verdict": "ok",
            "reason": (
                f"oops_count={oops_count} ; "
                f"warn_count={warn_count}. Quiet.")}


def status(config: Optional[dict] = None,
           oops: str = DEFAULT_OOPS,
           warn: str = DEFAULT_WARN,
           panic_on_oops_path: str = DEFAULT_PANIC_ON_OOPS
           ) -> dict:
    counters_present = (os.path.isfile(oops)
                        and os.path.isfile(warn))
    oops_count = _read_int(oops) if counters_present else None
    warn_count = _read_int(warn) if counters_present else None
    panic_on_oops = _read_int(panic_on_oops_path)
    verdict = classify(counters_present, oops_count,
                       warn_count, panic_on_oops)
    return {
        "ok": verdict["verdict"] == "ok",
        "oops_count": oops_count,
        "warn_count": warn_count,
        "panic_on_oops": panic_on_oops,
        "verdict": verdict,
    }
