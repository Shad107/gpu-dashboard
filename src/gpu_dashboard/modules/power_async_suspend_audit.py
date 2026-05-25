"""Module power_async_suspend_audit — /sys/power/* suspend
behavioural tunables (R&D #105.3).

Existing suspend modules (suspend_mode_selector_audit,
suspend_stats_audit, wakeup_sources_audit,
per_device_wakeup_attribution_audit) cover s2idle/deep
selection, suspend success stats, and wakeup-source
enumeration. None touch the *behavioural* tunables that
govern resume latency and dirty-data safety:

  /sys/power/pm_async               1 = parallel device suspend
                                    0 = single-threaded
                                        (slow resume)
  /sys/power/pm_freeze_timeout      ms ; default 20000.
                                    Too short trips
                                    'Freezing of tasks failed'
                                    with nvidia.ko or zfs.ko.
  /sys/power/sync_on_suspend        1 = sync before suspend,
                                    0 = unwritten pages lost
                                        on power-loss-during-
                                        suspend.
  /sys/power/pm_print_times         1 = noisy dmesg per device
                                        suspend timing.

Verdicts (worst-first) :

  sync_on_suspend_off_data_risk  warn   sync_on_suspend=0 —
                                        power-loss during
                                        suspend = unwritten
                                        pages lost.
  pm_async_off_slow_resume       warn   pm_async=0 — single-
                                        threaded device
                                        suspend on multi-GPU
                                        host, measurable
                                        > 2 s resume regression.
  freeze_timeout_short_nvidia    accent pm_freeze_timeout
                                        < 20000 — nvidia.ko /
                                        zfs.ko often need >20s
                                        to quiesce.
  pm_print_times_on_dmesg_noisy  accent pm_print_times=1 —
                                        floods kmsg ; turn off
                                        once tuning is done.
  ok                                     defaults.
  requires_root                          /sys/power/* unreadable.
  unknown                                /sys/power absent.

stdlib only.
"""
from __future__ import annotations

import os
from typing import Optional

NAME = "power_async_suspend_audit"

DEFAULT_POWER = "/sys/power"

_FREEZE_TIMEOUT_MIN_MS = 20000


def _read_int(path: str) -> Optional[int]:
    try:
        with open(path, "r", encoding="utf-8") as fh:
            return int(fh.read().strip())
    except (OSError, PermissionError, ValueError):
        return None


def classify(power_present: bool,
             pm_async: Optional[int],
             freeze_timeout: Optional[int],
             sync_on_suspend: Optional[int],
             pm_print_times: Optional[int]) -> dict:
    if not power_present:
        return {"verdict": "unknown",
                "reason": (
                    "/sys/power absent — kernel without "
                    "PM support.")}
    if (pm_async is None and freeze_timeout is None
            and sync_on_suspend is None
            and pm_print_times is None):
        return {"verdict": "requires_root",
                "reason": (
                    "/sys/power/* unreadable — re-run "
                    "as root.")}

    # warn — sync off = data risk
    if sync_on_suspend == 0:
        return {
            "verdict": "sync_on_suspend_off_data_risk",
            "reason": (
                "sync_on_suspend=0 — kernel won't sync dirty "
                "pages before suspending. Power loss during "
                "suspend = lost writes. Re-enable on any "
                "host you care about.")}

    # warn — pm_async off = slow resume
    if pm_async == 0:
        return {
            "verdict": "pm_async_off_slow_resume",
            "reason": (
                "pm_async=0 — device suspend runs single-"
                "threaded. Resume regresses noticeably on "
                "hosts with discrete GPU + NVMe + Wi-Fi.")}

    # accent — freeze timeout short
    if (freeze_timeout is not None
            and freeze_timeout < _FREEZE_TIMEOUT_MIN_MS):
        return {
            "verdict": "freeze_timeout_short_nvidia",
            "reason": (
                f"pm_freeze_timeout={freeze_timeout} ms "
                f"(< {_FREEZE_TIMEOUT_MIN_MS}). nvidia.ko / "
                "zfs.ko often need > 20s to quiesce ; suspend "
                "will fail with 'Freezing of tasks failed'.")}

    # accent — pm_print_times noisy
    if pm_print_times == 1:
        return {
            "verdict": "pm_print_times_on_dmesg_noisy",
            "reason": (
                "pm_print_times=1 — kernel logs every "
                "device suspend / resume time to dmesg. "
                "Useful during tuning, noisy in production.")}

    return {"verdict": "ok",
            "reason": (
                f"pm_async={pm_async} ; "
                f"freeze_timeout={freeze_timeout}ms ; "
                f"sync={sync_on_suspend} ; "
                f"print_times={pm_print_times}. Sane.")}


def status(config: Optional[dict] = None,
           power: str = DEFAULT_POWER) -> dict:
    power_present = os.path.isdir(power)
    pm_async = (
        _read_int(os.path.join(power, "pm_async"))
        if power_present else None)
    freeze_timeout = (
        _read_int(os.path.join(power, "pm_freeze_timeout"))
        if power_present else None)
    sync_on_suspend = (
        _read_int(os.path.join(power, "sync_on_suspend"))
        if power_present else None)
    pm_print_times = (
        _read_int(os.path.join(power, "pm_print_times"))
        if power_present else None)
    verdict = classify(power_present, pm_async,
                       freeze_timeout, sync_on_suspend,
                       pm_print_times)
    return {
        "ok": verdict["verdict"] == "ok",
        "pm_async": pm_async,
        "pm_freeze_timeout_ms": freeze_timeout,
        "sync_on_suspend": sync_on_suspend,
        "pm_print_times": pm_print_times,
        "verdict": verdict,
    }
