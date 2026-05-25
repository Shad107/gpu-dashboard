"""Module ksm_advisor_audit — KSM advisor + smart_scan
posture (R&D #101.4).

The existing ksm_audit covers basic /sys/kernel/mm/ksm/{run,
pages_to_scan, sleep_millisecs} + the merge/profit counters.
Kernel 6.7+ added two newer knobs that decide whether KSM
wastes CPU :

  advisor_mode             # none | scan-time
    'none'    = legacy static cadence (pages_to_scan +
                sleep_millisecs decided once at boot)
    'scan-time' = dynamic cadence aiming for a target
                  full-scan duration (advisor_target_scan_time)
  smart_scan               # 0/1
    0 = scan every page each pass, even ones that never
        merged (wasted CPU)
    1 = skip pages unlikely to merge

When KSM is *running* with advisor_mode=none + smart_scan=0,
the kernel can burn 5-15 % of one CPU walking every anonymous
page for nothing.

Reads :

  /sys/kernel/mm/ksm/run
  /sys/kernel/mm/ksm/advisor_mode
  /sys/kernel/mm/ksm/smart_scan
  /sys/kernel/mm/ksm/advisor_target_scan_time
  /sys/kernel/mm/ksm/advisor_max_cpu

No existing module checks the advisor knobs (ksm_audit only
reads run / pages_to_scan / sleep_millisecs / merge counters).

Verdicts (worst-first) :

  ksm_running_no_advisor    warn    KSM running with
                                    advisor_mode=none —
                                    legacy static cadence
                                    burns CPU on cold pages.
  ksm_smart_scan_off        accent  smart_scan=0 — scans
                                    every page each pass,
                                    even non-mergeable ones.
  ksm_target_too_aggressive accent  advisor_target_scan_time
                                    < 60 s — full-scan loop
                                    too aggressive.
  ok                                KSM off OR running with
                                    advisor + smart_scan.
  requires_root                     ksm sysfs unreadable.
  unknown                           /sys/kernel/mm/ksm
                                    absent.

stdlib only.
"""
from __future__ import annotations

import os
from typing import Optional

NAME = "ksm_advisor_audit"

DEFAULT_KSM_SYSFS = "/sys/kernel/mm/ksm"

_TARGET_SCAN_MIN_S = 60


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


def _read_str(path: str) -> Optional[str]:
    t = _read_text(path)
    return t.strip() if t is not None else None


def parse_advisor_mode(text: Optional[str]) -> Optional[str]:
    """Format is '[none] scan-time' or 'none [scan-time]'."""
    if not text:
        return None
    for tok in text.split():
        if tok.startswith("[") and tok.endswith("]"):
            return tok[1:-1]
    return None


def classify(sysfs_present: bool,
             run: Optional[int],
             advisor_mode: Optional[str],
             smart_scan: Optional[int],
             target_scan_time: Optional[int]) -> dict:
    if not sysfs_present:
        return {"verdict": "unknown",
                "reason": (
                    "/sys/kernel/mm/ksm absent — kernel "
                    "without CONFIG_KSM.")}
    if (run is None and advisor_mode is None
            and smart_scan is None):
        return {"verdict": "requires_root",
                "reason": (
                    "ksm sysfs unreadable — re-run as "
                    "root.")}

    # If KSM isn't running, no waste to audit
    if run == 0:
        return {"verdict": "ok",
                "reason": (
                    "ksm.run=0 — KSM stopped, nothing to "
                    "audit.")}

    # warn — running with legacy static cadence
    if advisor_mode == "none":
        return {
            "verdict": "ksm_running_no_advisor",
            "reason": (
                "KSM is running with advisor_mode=none — "
                "legacy static cadence (pages_to_scan + "
                "sleep_millisecs fixed). Wastes CPU on "
                "cold pages ; switch to scan-time advisor.")}

    # accent — smart_scan off
    if smart_scan == 0:
        return {
            "verdict": "ksm_smart_scan_off",
            "reason": (
                "smart_scan=0 — KSM rescans every page "
                "each pass, including pages that never "
                "merged. Idle CPU burn.")}

    # accent — target scan time too aggressive
    if (target_scan_time is not None
            and 0 < target_scan_time < _TARGET_SCAN_MIN_S):
        return {
            "verdict": "ksm_target_too_aggressive",
            "reason": (
                f"advisor_target_scan_time="
                f"{target_scan_time} s (< "
                f"{_TARGET_SCAN_MIN_S} s). Full-scan loop "
                "fires too often ; bump for less idle "
                "burn.")}

    return {"verdict": "ok",
            "reason": (
                f"ksm.run={run} ; advisor={advisor_mode} ; "
                f"smart_scan={smart_scan} ; "
                f"target_scan_time={target_scan_time}s. "
                "Sane.")}


def status(config: Optional[dict] = None,
           sysfs: str = DEFAULT_KSM_SYSFS) -> dict:
    sysfs_present = os.path.isdir(sysfs)
    run = (_read_int(os.path.join(sysfs, "run"))
           if sysfs_present else None)
    advisor_mode = parse_advisor_mode(
        _read_text(os.path.join(sysfs, "advisor_mode")))
    smart_scan = (
        _read_int(os.path.join(sysfs, "smart_scan"))
        if sysfs_present else None)
    target_scan_time = (
        _read_int(os.path.join(
            sysfs, "advisor_target_scan_time"))
        if sysfs_present else None)
    verdict = classify(sysfs_present, run, advisor_mode,
                       smart_scan, target_scan_time)
    return {
        "ok": verdict["verdict"] == "ok",
        "run": run,
        "advisor_mode": advisor_mode,
        "smart_scan": smart_scan,
        "advisor_target_scan_time": target_scan_time,
        "verdict": verdict,
    }
