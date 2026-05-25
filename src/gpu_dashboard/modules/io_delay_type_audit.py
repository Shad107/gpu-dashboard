"""Module io_delay_type_audit — x86 legacy port-I/O delay
mode (R&D #106.1).

The kernel uses an x86 'io delay' between legacy port-I/O
operations (inb/outb on 0xed, 0x80, etc.) to give old ISA
hardware time to settle. Modern kernels default to udelay,
but cmdline / sysctl can override:

  /proc/sys/kernel/io_delay_type
    0 = outb 0x80    (legacy ; ~1 µs per port-I/O on Zen4
                      / Raptor — shows up as ACPI path
                      latency)
    1 = udelay       (modern default)
    2 = 0xed         (alternative port)
    3 = none         (skip — risky if SMBus / EC drivers
                      assume settle time)

No existing module checks this — grep across all 300+
modules returns zero hits. Orthogonal to acpi_audit.

Reads :

  /proc/sys/kernel/io_delay_type

Verdicts (worst-first) :

  io_delay_none_risky      warn    type=3 (none) — SMBus /
                                   EC / RTC drivers can read
                                   garbage on quirky boards.
  io_delay_legacy_slow     accent  type=0 (outb 0x80) on a
                                   modern x86 — every legacy
                                   port-I/O adds ~1 µs.
  io_delay_default               ok  type=1 (udelay) or 2.
  requires_root                   sysctl unreadable.
  unknown                         /proc/sys/kernel/io_delay_type
                                  absent (non-x86 / sysctl
                                  removed).

stdlib only.
"""
from __future__ import annotations

import os
from typing import Optional

NAME = "io_delay_type_audit"

DEFAULT_PATH = "/proc/sys/kernel/io_delay_type"


def _read_int(path: str) -> Optional[int]:
    try:
        with open(path, "r", encoding="utf-8") as fh:
            return int(fh.read().strip())
    except (OSError, PermissionError, ValueError):
        return None


def classify(present: bool,
             value: Optional[int]) -> dict:
    if not present:
        return {"verdict": "unknown",
                "reason": (
                    "/proc/sys/kernel/io_delay_type absent "
                    "— non-x86 or sysctl removed.")}
    if value is None:
        return {"verdict": "requires_root",
                "reason": (
                    "io_delay_type unreadable — re-run "
                    "as root.")}

    if value == 3:
        return {
            "verdict": "io_delay_none_risky",
            "reason": (
                "io_delay_type=3 (none) — no settle delay "
                "between legacy port-I/O ops. SMBus / EC / "
                "RTC drivers on quirky consumer boards can "
                "read corrupted data.")}

    if value == 0:
        return {
            "verdict": "io_delay_legacy_slow",
            "reason": (
                "io_delay_type=0 (outb 0x80) — every legacy "
                "port-I/O adds ~1 µs on modern x86. Shows "
                "up as ACPI path latency. Default 1 (udelay) "
                "is fine.")}

    return {"verdict": "ok",
            "reason": (
                f"io_delay_type={value} — sane default.")}


def status(config: Optional[dict] = None,
           path: str = DEFAULT_PATH) -> dict:
    present = os.path.isfile(path)
    value = _read_int(path) if present else None
    verdict = classify(present, value)
    return {
        "ok": verdict["verdict"] == "ok",
        "io_delay_type": value,
        "verdict": verdict,
    }
