"""Module tracing_instances_audit — per-instance ftrace
ring-buffer auditor (R&D #96.3).

Three tracing modules touch related surface but only the
top-level buffer :

  * tracing_buffer_footprint_audit (#95.3) — root
    /sys/kernel/tracing/{buffer_*_size_kb,
    per_cpu/cpuN/stats, trace_clock}
  * ftrace_audit                          — root
    current_tracer / tracing_on / set_event
  * tracing_events_enable_audit           — per-event toggles

This audit walks /sys/kernel/tracing/instances/<name>/ for
the per-instance subtree. bpftrace / trace-cmd / perfetto
all create instances/<tool>/ and leak them when Ctrl-C'd —
the buffer keeps holding RAM until reboot.

Reads :

  /sys/kernel/tracing/instances/<name>/buffer_size_kb
  /sys/kernel/tracing/instances/<name>/tracing_on
  /sys/kernel/tracing/instances/<name>/current_tracer
  /sys/kernel/tracing/instances/<name>/set_event
                              (presence + size for context)

Verdicts (worst-first) :

  orphan_instance_burning_ram  err   any instance with
                                     tracing_on=1 AND
                                     buffer_size_kb × cpu_count
                                     > 256 MiB — leaked
                                     ring buffer eating RAM.
  instance_left_armed          warn  tracing_on=1 AND
                                     current_tracer != 'nop'
                                     — actively recording
                                     to a stale instance.
  many_instances               accent > 3 instances —
                                     accumulation of stale
                                     tool sessions.
  instances_clean              ok    no instances OR only
                                     idle ones.
  requires_root                tracefs mode-700.
  unknown                      tracing instances dir absent.

stdlib only.
"""
from __future__ import annotations

import os
from typing import Optional

NAME = "tracing_instances_audit"

DEFAULT_TRACING_ROOT = "/sys/kernel/tracing"

_ORPHAN_RAM_KB = 256 * 1024  # 256 MiB per instance × ncpus
_MANY_INSTANCES_THRESHOLD = 3


def _read_text(path: str) -> Optional[str]:
    try:
        with open(path, "r", encoding="utf-8") as fh:
            return fh.read().strip()
    except (OSError, PermissionError):
        return None


def _read_int(path: str) -> Optional[int]:
    t = _read_text(path)
    if t is None:
        return None
    try:
        return int(t)
    except ValueError:
        return None


def list_instances(
        root: str = DEFAULT_TRACING_ROOT) -> list:
    """Return list of instance names. Empty if dir missing
    OR mode-700 + unreadable."""
    instances_dir = os.path.join(root, "instances")
    if not os.path.isdir(instances_dir):
        return []
    try:
        return sorted(os.listdir(instances_dir))
    except (OSError, PermissionError):
        return []


def read_instance(root: str, name: str) -> dict:
    base = os.path.join(root, "instances", name)
    return {
        "name": name,
        "buffer_size_kb": _read_int(
            os.path.join(base, "buffer_size_kb")) or 0,
        "tracing_on": _read_int(
            os.path.join(base, "tracing_on")),
        "current_tracer": _read_text(
            os.path.join(base, "current_tracer")) or "",
    }


def classify(instances: list,
             cpu_count: int,
             root_exists: bool,
             dir_unreadable: bool) -> dict:
    if not root_exists:
        return {"verdict": "unknown",
                "reason": (
                    "/sys/kernel/tracing/instances absent — "
                    "kernel built without ftrace or tracefs "
                    "not mounted.")}
    if dir_unreadable:
        return {"verdict": "requires_root",
                "reason": (
                    "/sys/kernel/tracing/instances/ mode-700 "
                    "— re-run as root.")}
    if not instances:
        return {"verdict": "instances_clean",
                "reason": (
                    "No ftrace instances active — only the "
                    "root buffer in use.")}

    # err — orphan instance burning ram
    orphans: list = []
    for inst in instances:
        if inst.get("tracing_on") != 1:
            continue
        size_kb = inst["buffer_size_kb"]
        per_instance_kb = size_kb * cpu_count
        if per_instance_kb > _ORPHAN_RAM_KB:
            orphans.append((inst["name"], per_instance_kb))
    if orphans:
        names = [n for n, _ in orphans]
        biggest = max(orphans, key=lambda kv: kv[1])
        return {
            "verdict": "orphan_instance_burning_ram",
            "reason": (
                f"{len(orphans)} ftrace instance(s) with "
                f"tracing_on=1 AND buffer > 256 MiB total "
                f"(biggest: {biggest[0]} = "
                f"{biggest[1] / 1024:.0f} MiB). Likely a "
                "bpftrace / trace-cmd / perfetto session "
                "leaked after Ctrl-C."),
            "orphans": names}

    # warn — instance armed (tracing_on=1, tracer != nop)
    armed = [
        inst for inst in instances
        if inst.get("tracing_on") == 1
        and inst["current_tracer"]
        and inst["current_tracer"] != "nop"]
    if armed:
        names = [inst["name"] for inst in armed]
        return {
            "verdict": "instance_left_armed",
            "reason": (
                f"{len(armed)} ftrace instance(s) actively "
                f"recording (tracer != nop): {names}. "
                "Verify whether a debug tool is still "
                "consuming the trace_pipe.")}

    # accent — too many instances
    if len(instances) > _MANY_INSTANCES_THRESHOLD:
        names = [inst["name"] for inst in instances]
        return {
            "verdict": "many_instances",
            "reason": (
                f"{len(instances)} ftrace instances exist "
                f"({names[:5]}). Accumulation of stale tool "
                "sessions — clean up with rmdir under "
                "/sys/kernel/tracing/instances/.")}

    return {"verdict": "instances_clean",
            "reason": (
                f"{len(instances)} ftrace instance(s) ; "
                "none orphan-burning RAM, none actively "
                "armed.")}


def status(config: Optional[dict] = None,
           root: str = DEFAULT_TRACING_ROOT) -> dict:
    instances_dir = os.path.join(root, "instances")
    root_exists = os.path.isdir(instances_dir)
    dir_unreadable = False
    names: list = []
    if root_exists:
        try:
            names = sorted(os.listdir(instances_dir))
        except (OSError, PermissionError):
            dir_unreadable = True
    instances = [read_instance(root, n) for n in names]
    cpu_count = os.cpu_count() or 1
    verdict = classify(instances, cpu_count, root_exists,
                       dir_unreadable)
    return {
        "ok": verdict["verdict"] == "instances_clean",
        "instance_count": len(instances),
        "verdict": verdict,
    }
