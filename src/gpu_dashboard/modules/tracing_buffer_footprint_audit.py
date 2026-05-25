"""Module tracing_buffer_footprint_audit — ftrace ring-buffer
footprint + overrun detector (R&D #95.3).

Two existing tracing modules don't touch buffer state :

  * ftrace_audit                 — current_tracer / tracing_on
                                   / set_event posture
  * tracing_events_enable_audit  — per-event toggles

This audit owns the ring-buffer's RAM footprint, per-CPU
event drops (overrun), and trace_clock-vs-SMP coherency.

Reads :

  /sys/kernel/tracing/buffer_size_kb          per-CPU buffer
                                              size
  /sys/kernel/tracing/buffer_total_size_kb    total across CPUs
  /sys/kernel/tracing/trace_clock             local / global /
                                              x86-tsc / etc.
  /sys/kernel/tracing/tracing_on              0 / 1
  /sys/kernel/tracing/per_cpu/cpuN/stats      per-cpu overrun
                                              counter

Verdicts (worst-first) :

  buffer_overrun_active     err  tracing_on=1 AND any
                                 per_cpu/cpuN/stats shows
                                 overrun > 0 (events being
                                 dropped right now).
  buffer_total_over_512mb   warn buffer_total_size_kb >
                                 524 288 — invisible RAM
                                 hog left from a debug
                                 session.
  trace_clock_local_with_smp accent trace_clock = "local" on
                                 a >1-CPU host — bogus
                                 cross-CPU event ordering.
  trace_buffer_sane         ok   no overruns, sane footprint.
  requires_root             tracefs mode-700 (typical).
  unknown                   /sys/kernel/tracing absent.

stdlib only.
"""
from __future__ import annotations

import os
import re
from typing import Optional

NAME = "tracing_buffer_footprint_audit"

DEFAULT_TRACING_ROOT = "/sys/kernel/tracing"

_OVER_512MB_KB = 524288

# Format of trace_clock: "[local] global counter uptime perf
# mono mono_raw boot tai x86-tsc". Bracketed = selected.
_BRACKETED = re.compile(r"\[([^\]]+)\]")


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


def parse_selected_clock(text: str) -> str:
    m = _BRACKETED.search(text or "")
    return m.group(1) if m else ""


def parse_per_cpu_overrun(text: str) -> int:
    """per_cpu/cpuN/stats has lines like 'overrun: 123'."""
    if not text:
        return 0
    for line in text.splitlines():
        if line.startswith("overrun:"):
            parts = line.split(":", 1)
            if len(parts) == 2:
                try:
                    return int(parts[1].strip())
                except ValueError:
                    return 0
    return 0


def read_state(
        root: str = DEFAULT_TRACING_ROOT) -> dict:
    """Walk per_cpu/* for overrun aggregation."""
    state = {
        "buffer_size_kb": _read_int(
            os.path.join(root, "buffer_size_kb")),
        "buffer_total_size_kb": _read_int(
            os.path.join(root, "buffer_total_size_kb")),
        "trace_clock_raw": _read_text(
            os.path.join(root, "trace_clock")) or "",
        "tracing_on": _read_int(
            os.path.join(root, "tracing_on")),
        "any_unreadable": False,
        "total_overrun": 0,
    }
    # Walk per_cpu to sum overrun.
    per_cpu = os.path.join(root, "per_cpu")
    if os.path.isdir(per_cpu):
        try:
            entries = os.listdir(per_cpu)
        except OSError:
            entries = []
        for cpu in entries:
            stats_path = os.path.join(per_cpu, cpu, "stats")
            stats_text = _read_text(stats_path)
            if stats_text is None:
                state["any_unreadable"] = True
                continue
            state["total_overrun"] += parse_per_cpu_overrun(
                stats_text)
    # If we couldn't read the top-level files either, mark
    # unreadable so classify returns requires_root.
    if (state["buffer_size_kb"] is None
            and state["buffer_total_size_kb"] is None):
        state["any_unreadable"] = True
    return state


def classify(state: dict,
             cpu_count: int,
             root_present: bool) -> dict:
    if not root_present:
        return {"verdict": "unknown",
                "reason": (
                    "/sys/kernel/tracing absent — kernel "
                    "built without CONFIG_FTRACE or tracefs "
                    "not mounted.")}
    if state["any_unreadable"]:
        return {"verdict": "requires_root",
                "reason": (
                    "/sys/kernel/tracing/* mode-700 — re-run "
                    "as root.")}

    overrun = state["total_overrun"]
    if state["tracing_on"] == 1 and overrun > 0:
        return {
            "verdict": "buffer_overrun_active",
            "reason": (
                f"tracing_on=1 AND per-CPU overrun = "
                f"{overrun} — events being dropped right "
                "now. Bump buffer_size_kb or reduce event "
                "rate.")}

    total_kb = state.get("buffer_total_size_kb")
    if total_kb is not None and total_kb > _OVER_512MB_KB:
        return {
            "verdict": "buffer_total_over_512mb",
            "reason": (
                f"buffer_total_size_kb = {total_kb} KB "
                f"({total_kb / 1024:.0f} MB) — invisible "
                "RAM hog. Likely left from a debug session.")}

    clock = parse_selected_clock(
        state["trace_clock_raw"])
    if (clock == "local"
            and cpu_count > 1):
        return {
            "verdict": "trace_clock_local_with_smp",
            "reason": (
                f"trace_clock = 'local' on a {cpu_count}-CPU "
                "host — cross-CPU event timestamps will "
                "appear in wrong order. Switch to 'global' "
                "or 'x86-tsc' for correct ordering.")}

    return {"verdict": "trace_buffer_sane",
            "reason": (
                f"buffer_total = {total_kb} KB ; "
                f"trace_clock = '{clock or '?'}' ; "
                "no per-CPU overruns.")}


def status(config: Optional[dict] = None,
           root: str = DEFAULT_TRACING_ROOT) -> dict:
    root_present = os.path.isdir(root)
    state = (read_state(root) if root_present
             else {"any_unreadable": False,
                   "total_overrun": 0,
                   "buffer_size_kb": None,
                   "buffer_total_size_kb": None,
                   "trace_clock_raw": "",
                   "tracing_on": None})
    cpu_count = os.cpu_count() or 1
    verdict = classify(state, cpu_count, root_present)
    return {
        "ok": verdict["verdict"] == "trace_buffer_sane",
        "buffer_total_size_kb":
            state.get("buffer_total_size_kb"),
        "trace_clock":
            parse_selected_clock(state["trace_clock_raw"]),
        "tracing_on": state.get("tracing_on"),
        "total_overrun": state.get("total_overrun"),
        "verdict": verdict,
    }
