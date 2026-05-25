"""Module perf_sampling_limits_audit — perf/eBPF sampling
posture (R&D #100.3).

Four kernel.perf_event_* sysctls together decide whether
flame-graph profiling will silently down-throttle, refuse to
mmap ring buffers, or hand back truncated stacks. Existing
modules cover only perf_event_paranoid (the security gate);
the sampling-rate / mlock / stack-depth tunables are uncharted.

  kernel.perf_cpu_time_max_percent
    Cap on CPU% the perf subsystem will spend handling events.
    Kernel auto-decays max_sample_rate when it trips. <= 25 %
    with an auto-decayed rate = profiles will silently lose
    samples.

  kernel.perf_event_max_sample_rate
    Default 100000 Hz. If lower, the auto-decay has fired.

  kernel.perf_event_mlock_kb
    Per-uid mlock budget for perf ring buffers. Default ~516.
    < 1024 = realistic ring sizes EPERM under non-root.

  kernel.perf_event_max_stack
    Default 127. Lower truncates flamegraphs.

Existing modules (security_posture, userspace_hardening_sysctls)
only check perf_event_paranoid.

Reads :

  /proc/sys/kernel/perf_cpu_time_max_percent
  /proc/sys/kernel/perf_event_max_sample_rate
  /proc/sys/kernel/perf_event_mlock_kb
  /proc/sys/kernel/perf_event_max_stack

Verdicts (worst-first) :

  perf_throttle_25pct_floor_hit  err     cpu_time_max_percent
                                         <= 25 AND
                                         max_sample_rate <
                                         100000 (auto-decay
                                         fired ; profiles
                                         lose samples).
  perf_mlock_kb_starved          warn    perf_event_mlock_kb
                                         < 1024 — non-root
                                         mmap of perf rings
                                         will EPERM.
  perf_max_stack_shallow         accent  max_stack < 127 —
                                         flamegraphs
                                         truncated.
  ok                                     defaults intact.
  requires_root                          /proc/sys/kernel
                                         subtree unreadable.
  unknown                                kernel without
                                         /proc/sys/kernel.

stdlib only.
"""
from __future__ import annotations

import os
from typing import Optional

NAME = "perf_sampling_limits_audit"

DEFAULT_KERNEL_SYSCTL = "/proc/sys/kernel"

_DEFAULT_SAMPLE_RATE = 100000
_MLOCK_MIN_KB = 1024
_MAX_STACK_DEFAULT = 127


def _read_int(path: str) -> Optional[int]:
    try:
        with open(path, "r", encoding="utf-8") as fh:
            return int(fh.read().strip())
    except (OSError, PermissionError, ValueError):
        return None


def classify(sysctl_present: bool,
             cpu_time_max_pct: Optional[int],
             max_sample_rate: Optional[int],
             mlock_kb: Optional[int],
             max_stack: Optional[int]) -> dict:
    if not sysctl_present:
        return {"verdict": "unknown",
                "reason": (
                    "/proc/sys/kernel absent.")}
    if (cpu_time_max_pct is None
            and max_sample_rate is None
            and mlock_kb is None
            and max_stack is None):
        return {"verdict": "requires_root",
                "reason": (
                    "perf_event_* sysctls unreadable — "
                    "re-run as root.")}

    # err — throttle floor hit + sample rate auto-decayed
    if (cpu_time_max_pct is not None
            and cpu_time_max_pct <= 25
            and max_sample_rate is not None
            and max_sample_rate < _DEFAULT_SAMPLE_RATE):
        return {
            "verdict": "perf_throttle_25pct_floor_hit",
            "reason": (
                f"perf_cpu_time_max_percent="
                f"{cpu_time_max_pct} AND "
                f"max_sample_rate={max_sample_rate} "
                f"(< default {_DEFAULT_SAMPLE_RATE}) — "
                "kernel auto-decayed the rate, perf "
                "profiles will lose samples.")}

    # warn — mlock_kb starved
    if (mlock_kb is not None
            and mlock_kb < _MLOCK_MIN_KB):
        return {
            "verdict": "perf_mlock_kb_starved",
            "reason": (
                f"perf_event_mlock_kb={mlock_kb} "
                f"(< {_MLOCK_MIN_KB}). Non-root mmap of "
                "perf ring buffers will EPERM under "
                "realistic ring sizes — `perf record` "
                "fails without sudo.")}

    # accent — stack depth shallow
    if (max_stack is not None
            and max_stack < _MAX_STACK_DEFAULT):
        return {
            "verdict": "perf_max_stack_shallow",
            "reason": (
                f"perf_event_max_stack={max_stack} "
                f"(< default {_MAX_STACK_DEFAULT}). "
                "Flamegraphs will be truncated.")}

    return {"verdict": "ok",
            "reason": (
                f"cpu_time={cpu_time_max_pct}% ; "
                f"sample_rate={max_sample_rate} ; "
                f"mlock={mlock_kb} kB ; "
                f"max_stack={max_stack}. Sane.")}


def status(config: Optional[dict] = None,
           sysctl: str = DEFAULT_KERNEL_SYSCTL) -> dict:
    sysctl_present = os.path.isdir(sysctl)
    cpu_time_max_pct = (
        _read_int(os.path.join(sysctl,
                                "perf_cpu_time_max_percent"))
        if sysctl_present else None)
    max_sample_rate = (
        _read_int(os.path.join(sysctl,
                                "perf_event_max_sample_rate"))
        if sysctl_present else None)
    mlock_kb = (
        _read_int(os.path.join(sysctl,
                                "perf_event_mlock_kb"))
        if sysctl_present else None)
    max_stack = (
        _read_int(os.path.join(sysctl,
                                "perf_event_max_stack"))
        if sysctl_present else None)
    verdict = classify(
        sysctl_present, cpu_time_max_pct, max_sample_rate,
        mlock_kb, max_stack)
    return {
        "ok": verdict["verdict"] == "ok",
        "perf_cpu_time_max_percent": cpu_time_max_pct,
        "perf_event_max_sample_rate": max_sample_rate,
        "perf_event_mlock_kb": mlock_kb,
        "perf_event_max_stack": max_stack,
        "verdict": verdict,
    }
