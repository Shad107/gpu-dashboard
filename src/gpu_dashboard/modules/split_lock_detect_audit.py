"""Module split_lock_detect_audit — Intel split-lock mitigation
posture (R&D #99.2).

A split lock is an atomic operation (LOCK CMPXCHG, XADD, etc.)
that straddles a cache-line boundary. The cost is ~1000x a
normal atomic — the CPU has to lock the bus, not just the
line. Wine/Proton games, older JVMs, V8, and some BLAS kernels
routinely emit them.

The kernel can :

  off       — ignore, silently bleed ~10-15% perf
  warn      — log + rate-limit (default in many distros)
  fatal     — SIGBUS the offending task ; Steam/Proton games
              get killed
  ratelimit:N — limit warnings to N/s

Modes are set via cmdline `split_lock_detect=` and/or the
`kernel.split_lock_mitigate` sysctl (0=off, 1=enabled).

No existing module audits this (cpu_vulnerabilities_audit,
cmdline_audit parse different tokens).

Reads :

  /proc/cpuinfo                          (vendor_id)
  /proc/cmdline                          (split_lock_detect=)
  /proc/sys/kernel/split_lock_mitigate   (0/1)

Verdicts (worst-first) :

  split_lock_fatal       err     `split_lock_detect=fatal` —
                                 Steam/Proton games SIGBUS.
  split_lock_off         warn    `split_lock_detect=off` or
                                 sysctl=0 — silent perf loss.
  split_lock_ratelimited accent  `ratelimit:N` — intent to
                                 tolerate ; verify dmesg.
  ok                             default warn mode active.
  requires_root                  sysctl unreadable.
  unknown                        non-Intel CPU OR sysctl
                                 absent (feature not exposed).

stdlib only.
"""
from __future__ import annotations

import os
from typing import Optional

NAME = "split_lock_detect_audit"

DEFAULT_CPUINFO = "/proc/cpuinfo"
DEFAULT_CMDLINE = "/proc/cmdline"
DEFAULT_MITIGATE_SYSCTL = (
    "/proc/sys/kernel/split_lock_mitigate")


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


def is_intel(cpuinfo_text: Optional[str]) -> bool:
    """Return True if vendor_id is GenuineIntel."""
    if not cpuinfo_text:
        return False
    for line in cpuinfo_text.splitlines():
        if line.startswith("vendor_id"):
            return "Intel" in line
    return False


def parse_cmdline_mode(cmdline: Optional[str]) -> Optional[str]:
    """Return the value of split_lock_detect= or None."""
    if not cmdline:
        return None
    for tok in cmdline.split():
        if tok.startswith("split_lock_detect="):
            return tok.split("=", 1)[1]
    return None


def classify(intel: bool,
             cmdline_mode: Optional[str],
             sysctl_mitigate: Optional[int],
             sysctl_present: bool) -> dict:
    if not intel:
        return {"verdict": "unknown",
                "reason": (
                    "Non-Intel CPU — split-lock detection "
                    "is an Intel-only feature.")}
    if not sysctl_present:
        return {"verdict": "unknown",
                "reason": (
                    "/proc/sys/kernel/split_lock_mitigate "
                    "absent — kernel doesn't expose the "
                    "feature.")}
    if sysctl_mitigate is None:
        return {"verdict": "requires_root",
                "reason": (
                    "split_lock_mitigate unreadable — "
                    "re-run as root.")}

    # err — fatal mode
    if cmdline_mode == "fatal":
        return {
            "verdict": "split_lock_fatal",
            "reason": (
                "split_lock_detect=fatal on cmdline — any "
                "task that emits a split lock gets SIGBUS. "
                "Steam/Proton games + Wine + old JVMs will "
                "die.")}

    # warn — explicitly off OR sysctl=0
    if cmdline_mode == "off" or sysctl_mitigate == 0:
        return {
            "verdict": "split_lock_off",
            "reason": (
                f"split-lock detection disabled "
                f"(cmdline={cmdline_mode}, sysctl="
                f"{sysctl_mitigate}). Wine / JVM / BLAS code "
                "silently bleeds ~10-15 % per offending op.")}

    # accent — ratelimit:N
    if cmdline_mode and cmdline_mode.startswith("ratelimit:"):
        return {
            "verdict": "split_lock_ratelimited",
            "reason": (
                f"split_lock_detect={cmdline_mode} — "
                "warnings rate-limited ; verify dmesg "
                "for actual offenders.")}

    return {"verdict": "ok",
            "reason": (
                f"split_lock_detect={cmdline_mode or 'warn'} "
                f"; sysctl={sysctl_mitigate}. Default warn "
                "mode is correct for a desktop.")}


def status(config: Optional[dict] = None,
           cpuinfo: str = DEFAULT_CPUINFO,
           cmdline_path: str = DEFAULT_CMDLINE,
           sysctl: str = DEFAULT_MITIGATE_SYSCTL) -> dict:
    intel = is_intel(_read_text(cpuinfo))
    cmdline_mode = parse_cmdline_mode(
        _read_text(cmdline_path))
    sysctl_present = os.path.isfile(sysctl)
    sysctl_mitigate = (_read_int(sysctl)
                       if sysctl_present else None)
    verdict = classify(intel, cmdline_mode,
                       sysctl_mitigate, sysctl_present)
    return {
        "ok": verdict["verdict"] == "ok",
        "intel": intel,
        "cmdline_mode": cmdline_mode,
        "sysctl_mitigate": sysctl_mitigate,
        "verdict": verdict,
    }
