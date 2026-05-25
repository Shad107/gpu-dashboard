"""Module printk_pacing_audit — printk_delay + devkmsg policy
(R&D #106.2).

The kernel's printk pacing knobs are separate from the loglevel
+ ratelimit *counters* covered by kmsg_audit. Specifically :

  /proc/sys/kernel/printk_delay
    ms ; default 0. NON-ZERO inserts an mdelay() between every
    kernel log line — a noisy driver (nvidia load, nvme reset
    storm) can wedge a CPU for seconds.

  /proc/sys/kernel/printk_devkmsg
    on | off | ratelimit. 'off' silently drops journald drain.

  /proc/sys/kernel/printk_ratelimit_burst
    Burst budget. < 5 cuts off legitimate dmesg bursts during
    GPU init.

No existing module reads printk_delay (grep zero hits). kmsg_audit
reads printk (loglevel), printk_ratelimit, printk_ratelimit_burst,
printk_devkmsg — but doesn't classify on printk_delay or on burst
< 5.

Verdicts (worst-first) :

  printk_delay_set         warn    printk_delay > 0 — every
                                   kernel log line stalls the
                                   logger CPU for that many
                                   ms.
  printk_devkmsg_off       warn    devkmsg=off — userspace
                                   dmesg readers silently lose
                                   data.
  ratelimit_burst_tiny     accent  printk_ratelimit_burst < 5
                                   — legitimate dmesg bursts
                                   get clipped.
  ok                               defaults.
  requires_root                    sysctl unreadable.
  unknown                          /proc/sys/kernel absent.

stdlib only.
"""
from __future__ import annotations

import os
from typing import Optional

NAME = "printk_pacing_audit"

DEFAULT_SYSCTL = "/proc/sys/kernel"

_BURST_MIN = 5


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


def classify(present: bool,
             delay: Optional[int],
             devkmsg: Optional[str],
             burst: Optional[int]) -> dict:
    if not present:
        return {"verdict": "unknown",
                "reason": "/proc/sys/kernel absent."}
    if (delay is None and devkmsg is None
            and burst is None):
        return {"verdict": "requires_root",
                "reason": (
                    "printk pacing knobs unreadable — "
                    "re-run as root.")}

    # warn — printk_delay set
    if delay is not None and delay > 0:
        return {
            "verdict": "printk_delay_set",
            "reason": (
                f"printk_delay={delay} ms — every kernel "
                "log line stalls the logger CPU. A noisy "
                "driver (nvidia load, nvme reset storm) "
                "can wedge for seconds.")}

    # warn — devkmsg off
    if devkmsg == "off":
        return {
            "verdict": "printk_devkmsg_off",
            "reason": (
                "printk_devkmsg=off — userspace dmesg "
                "readers (journald, rsyslogd) silently "
                "lose kernel messages.")}

    # accent — burst too small
    if burst is not None and 0 < burst < _BURST_MIN:
        return {
            "verdict": "ratelimit_burst_tiny",
            "reason": (
                f"printk_ratelimit_burst={burst} (< "
                f"{_BURST_MIN}) — legitimate dmesg "
                "bursts during GPU init get clipped.")}

    return {"verdict": "ok",
            "reason": (
                f"printk_delay={delay} ms ; "
                f"devkmsg={devkmsg} ; "
                f"burst={burst}. Sane.")}


def status(config: Optional[dict] = None,
           sysctl: str = DEFAULT_SYSCTL) -> dict:
    present = os.path.isdir(sysctl)
    delay = (
        _read_int(os.path.join(sysctl, "printk_delay"))
        if present else None)
    devkmsg = (
        _read_str(os.path.join(sysctl, "printk_devkmsg"))
        if present else None)
    burst = (
        _read_int(os.path.join(
            sysctl, "printk_ratelimit_burst"))
        if present else None)
    verdict = classify(present, delay, devkmsg, burst)
    return {
        "ok": verdict["verdict"] == "ok",
        "printk_delay_ms": delay,
        "printk_devkmsg": devkmsg,
        "printk_ratelimit_burst": burst,
        "verdict": verdict,
    }
