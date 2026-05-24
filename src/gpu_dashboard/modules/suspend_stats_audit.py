"""Module suspend_stats_audit — S3 / S2idle suspend
success-rate audit (R&D #84.1).

Reads /sys/power/suspend_stats/ — the kernel's cumulative
counters for system-wide suspend / hibernate cycles plus
per-step failure breakdown :

  success                total successful suspend cycles
  fail                   total failed cycles
  failed_freeze          freezing tasks failed
  failed_prepare         driver prepare phase failed
  failed_suspend         core suspend phase failed
  failed_suspend_late
  failed_suspend_noirq
  failed_resume          one of resume phases failed
  failed_resume_early
  failed_resume_noirq
  last_failed_dev        driver name of the last failure
  last_failed_errno      kernel errno (-EBUSY, -EIO, …)
  last_failed_step       freeze / prepare / suspend / …

Homelab desktops commonly silently fail S3/S2idle resume on
a specific driver — NVIDIA, USB hub re-enumeration, a flaky
SATA controller.  One read tells you whether suspend ever
worked and exactly which device/step broke it last time.

Verdicts (worst first) :

  suspend_failing          last_failed_errno != 0 AND
                           fail >= 5 — recent sustained
                           failures, fix the driver.
  suspend_had_failures     fail > 0 OR any failed_* counter
                           > 0 — at least one failure in the
                           cycle history.
  suspend_never_exercised  success = 0 AND fail = 0 — the
                           host has never attempted a
                           suspend cycle (informational on
                           always-on rigs, worth knowing on
                           a laptop / desktop).
  ok                       success > 0, all failure counters
                           at zero.
  unknown                  /sys/power/suspend_stats absent
                           (kernel < 5.18, or no PM support).
"""
from __future__ import annotations

import os
from typing import Optional

DEFAULT_SUSPEND_STATS = "/sys/power/suspend_stats"

_FAILED_STEPS = (
    "failed_freeze", "failed_prepare",
    "failed_suspend", "failed_suspend_late",
    "failed_suspend_noirq",
    "failed_resume", "failed_resume_early",
    "failed_resume_noirq",
)

# Thresholds
_FAILING_RECENT_FLOOR = 5


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


def read_stats(root: str = DEFAULT_SUSPEND_STATS) -> dict:
    """Returns flat dict ; missing files become None."""
    out: dict = {
        "present": os.path.isdir(root),
        "success": _read_int(os.path.join(root, "success")),
        "fail": _read_int(os.path.join(root, "fail")),
        "last_failed_dev": _read_text(
            os.path.join(root, "last_failed_dev")) or "",
        "last_failed_errno": _read_int(
            os.path.join(root, "last_failed_errno")),
        "last_failed_step": _read_text(
            os.path.join(root, "last_failed_step")) or "",
    }
    for step in _FAILED_STEPS:
        out[step] = _read_int(os.path.join(root, step))
    return out


def classify(stats: dict) -> dict:
    if not stats.get("present"):
        return {"verdict": "unknown",
                "reason": (
                    "/sys/power/suspend_stats absent — "
                    "kernel < 5.18 or no PM-suspend "
                    "support.")}

    success = stats.get("success") or 0
    fail = stats.get("fail") or 0
    errno = stats.get("last_failed_errno") or 0
    dev = stats.get("last_failed_dev") or ""
    step = stats.get("last_failed_step") or ""

    step_fail_total = sum(
        (stats.get(s) or 0) for s in _FAILED_STEPS)

    # 1. err — recent sustained failures
    if errno != 0 and fail >= _FAILING_RECENT_FLOOR:
        return {"verdict": "suspend_failing",
                "reason": (
                    f"{fail} suspend failures recorded ; "
                    f"last_failed_errno = {errno} on "
                    f"{dev or '<unknown>'} during step "
                    f"{step or '<unknown>'}."),
                "fail": fail, "errno": errno,
                "dev": dev, "step": step}

    # 2. warn — at least one failure ever
    if fail > 0 or step_fail_total > 0:
        return {"verdict": "suspend_had_failures",
                "reason": (
                    f"{fail} fail(s) of {success + fail} "
                    "cycle(s) historically ; last failure "
                    f"on {dev or '<unknown>'} step "
                    f"{step or '<unknown>'}."),
                "fail": fail, "success": success,
                "dev": dev, "step": step}

    # 3. accent — never exercised
    if success == 0 and fail == 0:
        return {"verdict": "suspend_never_exercised",
                "reason": (
                    "Suspend has never been attempted on "
                    "this host (success = 0, fail = 0). "
                    "Test once before relying on it.")}

    # 4. ok
    return {"verdict": "ok",
            "reason": (
                f"{success} suspend cycle(s) succeeded, "
                "no failures recorded.")}


def status(config: Optional[dict] = None,
           root: str = DEFAULT_SUSPEND_STATS) -> dict:
    stats = read_stats(root)
    verdict = classify(stats)
    return {
        "ok": verdict["verdict"] not in (
            "unknown", "suspend_failing"),
        "success": stats.get("success"),
        "fail": stats.get("fail"),
        "last_failed_dev": stats.get("last_failed_dev"),
        "last_failed_errno": stats.get("last_failed_errno"),
        "last_failed_step": stats.get("last_failed_step"),
        "verdict": verdict,
    }
