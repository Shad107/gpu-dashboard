"""Module hung_task_drift_audit — kernel hung-task detection
drift (R&D #104.2).

Linux detects D-state tasks stuck for more than
hung_task_timeout_secs and emits a stack dump. After emitting
hung_task_warnings (default 10) messages it goes *silent* —
the counter decrements toward 0 and never resets. By morning,
a host that hit 10 hung tasks during a kernel-driver storm
is no longer logging the 11th, 12th, 50th…

  /proc/sys/kernel/hung_task_warnings
    Decrementing budget. 0 = silent.

  /proc/sys/kernel/hung_task_check_interval_secs
    0 = check on every timeout window ; large = check rarely.

The existing panic_policy module enumerates `hung_task_warnings`
in its sysctl list but doesn't classify on exhaustion or
check-interval drift. kernel_lockup_watchdog_audit covers
hardlockup / softlockup, not D-state hangs.

Reads :

  /proc/sys/kernel/hung_task_warnings
  /proc/sys/kernel/hung_task_check_interval_secs
  /proc/sys/kernel/hung_task_timeout_secs

Verdicts (worst-first) :

  hung_task_warnings_exhausted    warn   counter at 0 — kernel
                                         is silent on new
                                         D-state hangs.
  hung_task_check_interval_long   accent check_interval_secs
                                         > 120 — late detection.
  hung_task_warnings_decayed      accent counter < 10 — burnt
                                         through warnings this
                                         boot, soon silent.
  ok                                     warnings >= 10,
                                         check_interval sane.
  requires_root                          knobs unreadable.
  unknown                                CONFIG_DETECT_HUNG_TASK
                                         disabled.

stdlib only.
"""
from __future__ import annotations

import os
from typing import Optional

NAME = "hung_task_drift_audit"

DEFAULT_SYSCTL = "/proc/sys/kernel"

_WARNINGS_DEFAULT = 10
_CHECK_INTERVAL_MAX = 120


def _read_int(path: str) -> Optional[int]:
    try:
        with open(path, "r", encoding="utf-8") as fh:
            return int(fh.read().strip())
    except (OSError, PermissionError, ValueError):
        return None


def classify(knobs_present: bool,
             warnings: Optional[int],
             check_interval: Optional[int],
             timeout: Optional[int]) -> dict:
    if not knobs_present:
        return {"verdict": "unknown",
                "reason": (
                    "CONFIG_DETECT_HUNG_TASK disabled — "
                    "hung_task sysctls absent.")}
    if warnings is None:
        return {"verdict": "requires_root",
                "reason": (
                    "hung_task_warnings unreadable — "
                    "re-run as root.")}

    # warn — warnings exhausted (kernel silent)
    if warnings == 0:
        return {
            "verdict": "hung_task_warnings_exhausted",
            "reason": (
                "hung_task_warnings=0 — kernel has burned "
                "through its hang-detection budget. New "
                "D-state hangs will be silent. Restore "
                "with `sysctl -w kernel.hung_task_warnings=10`.")}

    # accent — check_interval too long
    if (check_interval is not None
            and check_interval > _CHECK_INTERVAL_MAX):
        return {
            "verdict": "hung_task_check_interval_long",
            "reason": (
                f"hung_task_check_interval_secs="
                f"{check_interval} (> "
                f"{_CHECK_INTERVAL_MAX}). Late detection ; "
                "hang sits unreported for minutes.")}

    # accent — counter decayed below default
    if warnings < _WARNINGS_DEFAULT:
        return {
            "verdict": "hung_task_warnings_decayed",
            "reason": (
                f"hung_task_warnings={warnings} (< default "
                f"{_WARNINGS_DEFAULT}). Some hangs already "
                "logged this boot ; budget nearly gone.")}

    return {"verdict": "ok",
            "reason": (
                f"hung_task_warnings={warnings} ; "
                f"check_interval={check_interval}s ; "
                f"timeout={timeout}s. Sane.")}


def status(config: Optional[dict] = None,
           sysctl: str = DEFAULT_SYSCTL) -> dict:
    warnings_path = os.path.join(sysctl, "hung_task_warnings")
    knobs_present = os.path.isfile(warnings_path)
    warnings = _read_int(warnings_path) if knobs_present else None
    check_interval = (
        _read_int(os.path.join(
            sysctl, "hung_task_check_interval_secs"))
        if knobs_present else None)
    timeout = (
        _read_int(os.path.join(
            sysctl, "hung_task_timeout_secs"))
        if knobs_present else None)
    verdict = classify(knobs_present, warnings,
                       check_interval, timeout)
    return {
        "ok": verdict["verdict"] == "ok",
        "hung_task_warnings": warnings,
        "hung_task_check_interval_secs": check_interval,
        "hung_task_timeout_secs": timeout,
        "verdict": verdict,
    }
