"""Module kernel_taint — taint flag + uptime correlator (R&D #36.3).

`/proc/sys/kernel/tainted` is a bitmask the kernel sets whenever it
loads something it doesn't fully trust. For NVIDIA-proprietary
hosts this is virtually never zero — but the difference between
"the GPU driver tainted the kernel" (expected) and "a hardware
machine-check tainted the kernel" (your CPU is dying) is the
diagnostic the user actually needs.

This module reads the int, decodes the bits, and classifies:

  clean             value == 0
  nvidia_normal     bits ⊆ {9, 12, 13} — the W+O+E signature of
                    a loaded out-of-tree unsigned module (the
                    NVIDIA proprietary driver on Debian)
  warnings          bit 9 set alone — kernel issued WARN_ON, look
                    at dmesg
  hardware_errors   bit 4 (M: machine check) OR bit 14 (L: soft
                    lockup) — serious, blame the CPU or runaway
                    softirq, not the GPU
  mixed             other unusual combination — surface decoded
                    flags so the user can investigate

Reference: Documentation/admin-guide/tainted-kernels.rst.

Also surfaces /proc/uptime so the user can correlate "tainted
since boot N days ago" against external events.

stdlib only.
"""
from __future__ import annotations

import os
from typing import Optional


NAME = "kernel_taint"


_PROC = "/proc"


# Bit → (code, description). Conventions per
# Documentation/admin-guide/tainted-kernels.rst (kernel 6.x).
_FLAGS: dict = {
    0: ("G/P", "proprietary module loaded"),
    1: ("F", "module forced-loaded"),
    2: ("S", "SMP on a CPU not designed for SMP"),
    3: ("R", "user forced module unload"),
    4: ("M", "machine check exception fired"),
    5: ("B", "system has bad page reference"),
    6: ("U", "user requested via sysctl"),
    7: ("D", "module unload error / death"),
    8: ("A", "ACPI table overridden"),
    9: ("W", "kernel issued a WARN_ON warning"),
    10: ("C", "staging driver loaded"),
    11: ("I", "firmware workaround in effect"),
    12: ("O", "out-of-tree module loaded"),
    13: ("E", "unsigned module loaded"),
    14: ("L", "soft lockup occurred"),
    15: ("K", "kernel live-patched"),
    16: ("X", "auxiliary taint (distribution-defined)"),
    17: ("T", "kernel built with RANDSTRUCT"),
    18: ("N", "kernel test only"),
}


def parse_taint_bits(value: int) -> list:
    out: list = []
    for bit in range(64):
        if value & (1 << bit):
            out.append(bit)
    return out


def flag_name(bit: int) -> dict:
    code, desc = _FLAGS.get(bit, ("?", f"unknown taint bit {bit}"))
    return {"bit": bit, "code": code, "description": desc}


_NVIDIA_NORMAL_BITS = {9, 12, 13}    # W, O, E
_HARDWARE_BITS = {4, 14}              # M, L


def classify(value: int, bits: list) -> dict:
    if value == 0:
        return {"verdict": "clean",
                "reason": "Kernel is untainted.",
                "recommendation": ""}
    bset = set(bits)
    if bset == {9}:
        return {"verdict": "warnings",
                "reason": ("Only bit 9 (W) set — kernel issued a "
                           "WARN_ON somewhere. Inspect dmesg to see "
                           "what triggered it."),
                "recommendation": (
                    "# Find the warning:\n"
                    "dmesg --color=never | grep -B 2 'WARNING:' | tail -30"
                )}
    if bset & _HARDWARE_BITS:
        codes = [flag_name(b)["code"] for b in sorted(bset & _HARDWARE_BITS)]
        return {"verdict": "hardware_errors",
                "reason": (f"Kernel taint includes {','.join(codes)} — "
                           f"machine check or soft lockup. This is a "
                           f"hardware-level alarm ; CPU, RAM, or kernel "
                           f"lock, NOT the GPU."),
                "recommendation": (
                    "# Pull recent kernel events:\n"
                    "dmesg --color=never | grep -E "
                    "'WARNING|machine check|soft lockup|hard lockup' | tail\n"
                    "# Re-check uptime — does the timestamp align with a\n"
                    "# crash window?\n"
                    "uptime")}
    if bset and bset <= _NVIDIA_NORMAL_BITS and (bset & {12, 13}):
        return {"verdict": "nvidia_normal",
                "reason": (f"Taint bits are subset of W/O/E "
                           f"({sorted(bset)}) — the canonical "
                           f"fingerprint of an out-of-tree unsigned "
                           f"module, i.e. the NVIDIA proprietary driver "
                           f"on Debian/Ubuntu. Expected, not a concern."),
                "recommendation": ""}
    codes = [flag_name(b)["code"] for b in sorted(bset)]
    return {"verdict": "mixed",
            "reason": (f"Taint flags = {','.join(codes)} — unusual "
                       f"combination. Investigate the non-NVIDIA flags."),
            "recommendation": (
                "# See decoded flags in the card. Inspect dmesg:\n"
                "dmesg --color=never | grep -E 'Tainted|WARNING' | tail")}


def _read(p: str) -> Optional[str]:
    try:
        with open(p) as f:
            return f.read().strip()
    except OSError:
        return None


def read_tainted(proc_root: str = _PROC) -> Optional[int]:
    s = _read(os.path.join(proc_root, "sys", "kernel", "tainted"))
    if s is None:
        return None
    try:
        return int(s)
    except ValueError:
        return None


def read_uptime(proc_root: str = _PROC) -> Optional[float]:
    s = _read(os.path.join(proc_root, "uptime"))
    if s is None:
        return None
    parts = s.split()
    if not parts:
        return None
    try:
        return float(parts[0])
    except ValueError:
        return None


def status(cfg=None) -> dict:
    value = read_tainted(_PROC)
    if value is None:
        return {"ok": False, "error": "tainted_unavailable",
                "reason": f"{_PROC}/sys/kernel/tainted not readable."}
    bits = parse_taint_bits(value)
    flags = [flag_name(b) for b in bits]
    uptime = read_uptime(_PROC)
    verdict = classify(value, bits)
    return {
        "ok": True,
        "value": value,
        "flags": flags,
        "uptime_seconds": uptime,
        "verdict": verdict,
    }
