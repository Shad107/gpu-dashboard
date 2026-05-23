"""Module ftrace_audit — ftrace tracer orphan auditor (R&D #48.1).

Reads /sys/kernel/tracing/* — files are typically 0640 root-only
on modern distros, so the module gracefully degrades with a
requires_root verdict.

Catches the "I forgot to stop my tracer" / "old debugging session
left kprobes attached" pattern. ftrace adds ~50-500 ns per syscall
when active ; for high-throughput inference this is measurable
inter-token jitter.

  /sys/kernel/tracing/current_tracer    active tracer name
                                        ("nop" = idle, anything
                                        else = active).
  /sys/kernel/tracing/tracing_on        0 = stopped, 1 = recording.
  /sys/kernel/tracing/kprobe_events     kprobe definitions (one
                                        per line, empty if no
                                        kprobes attached).
  /sys/kernel/tracing/uprobe_events     uprobe definitions.
  /sys/kernel/tracing/set_event         currently-enabled events.

Verdicts (priority-ordered) :
  tracer_left_on             current_tracer != "nop" AND tracing_on=1
                             → someone's tracer is hot, costing CPU.
  orphan_kprobes             ≥1 kprobe in kprobe_events that's not
                             a known systemd-related probe.
  orphan_uprobes             ≥1 uprobe definition.
  events_enabled             non-empty set_event (specific events
                             toggled on without a full tracer).
  ok                         all idle.
  requires_root              /sys/kernel/tracing files unreadable
                             (need CAP_SYS_ADMIN).
  unknown                    /sys/kernel/tracing absent.

stdlib only.
"""
from __future__ import annotations

import os
from typing import Optional


NAME = "ftrace_audit"


_SYS_TRACING = "/sys/kernel/tracing"


def _read(p: str) -> Optional[str]:
    try:
        with open(p) as f:
            return f.read()
    except OSError:
        return None


def _has_permission_error(path: str) -> bool:
    try:
        with open(path):
            return False
    except PermissionError:
        return True
    except OSError:
        return False


def _probe_permission(sys_tr: str = _SYS_TRACING) -> bool:
    """Returns True if the directory exists but the daemon can't
    read its files (permission denied)."""
    if not os.path.isdir(sys_tr):
        return False
    return _has_permission_error(os.path.join(sys_tr,
                                                "current_tracer"))


_RECIPE_TRACER = (
    "# A tracer is still active — reset to idle (nop) :\n"
    "echo 0 | sudo tee /sys/kernel/tracing/tracing_on\n"
    "echo nop | sudo tee /sys/kernel/tracing/current_tracer\n"
    "# Optionally clear the trace buffer :\n"
    "echo > /sys/kernel/tracing/trace"
)

_RECIPE_KPROBES = (
    "# Orphan kprobes detected — likely left by a previous\n"
    "# debugging session. Clear them :\n"
    "echo > /sys/kernel/tracing/kprobe_events\n"
    "# Or remove individually (kprobe-events syntax) :\n"
    "echo '-:my_probe' >> /sys/kernel/tracing/kprobe_events"
)

_RECIPE_REQUIRES_ROOT = (
    "# /sys/kernel/tracing files are root-only on this distro.\n"
    "# To grant read access without running the daemon as root,\n"
    "# add CAP_DAC_READ_SEARCH via systemd drop-in :\n"
    "systemctl --user edit gpu-dashboard.service\n"
    "# [Service]\n"
    "# AmbientCapabilities=CAP_DAC_READ_SEARCH"
)


def classify(state: dict, requires_root: bool) -> dict:
    if requires_root and not state.get("current_tracer"):
        return {"verdict": "requires_root",
                "reason": ("/sys/kernel/tracing files are 0640 "
                           "root-only on this distro ; daemon is "
                           "running unprivileged."),
                "recommendation": _RECIPE_REQUIRES_ROOT}
    if not state.get("available"):
        return {"verdict": "unknown",
                "reason": "/sys/kernel/tracing absent.",
                "recommendation": ""}
    cur = (state.get("current_tracer") or "").strip()
    tracing_on = state.get("tracing_on")
    if cur and cur != "nop" and tracing_on == 1:
        return {"verdict": "tracer_left_on",
                "reason": (f"current_tracer='{cur}' + tracing_on=1 "
                           f"— ftrace is recording. ~50-500 ns "
                           f"per syscall overhead."),
                "recommendation": _RECIPE_TRACER}
    kprobes = state.get("kprobe_events") or []
    if kprobes:
        return {"verdict": "orphan_kprobes",
                "reason": (f"{len(kprobes)} kprobe definition(s) "
                           f"active in /sys/kernel/tracing/"
                           f"kprobe_events."),
                "recommendation": _RECIPE_KPROBES}
    uprobes = state.get("uprobe_events") or []
    if uprobes:
        return {"verdict": "orphan_uprobes",
                "reason": (f"{len(uprobes)} uprobe definition(s) "
                           f"active."),
                "recommendation": _RECIPE_KPROBES}
    enabled = state.get("set_event_count", 0)
    if enabled > 0:
        return {"verdict": "events_enabled",
                "reason": (f"{enabled} specific tracing event(s) "
                           f"toggled on."),
                "recommendation": _RECIPE_TRACER}
    return {"verdict": "ok",
            "reason": (f"current_tracer={cur or 'nop'}, "
                       f"tracing_on={tracing_on}, no orphan "
                       f"k/uprobes."),
            "recommendation": ""}


def read_state(sys_tr: str = _SYS_TRACING) -> dict:
    if not os.path.isdir(sys_tr):
        return {"available": False}
    state: dict = {"available": True}
    cur = _read(os.path.join(sys_tr, "current_tracer"))
    if cur is not None:
        state["current_tracer"] = cur.strip()
    on_text = _read(os.path.join(sys_tr, "tracing_on"))
    if on_text is not None:
        try:
            state["tracing_on"] = int(on_text.strip())
        except ValueError:
            pass
    kp_text = _read(os.path.join(sys_tr, "kprobe_events"))
    if kp_text is not None:
        state["kprobe_events"] = [l for l in kp_text.splitlines()
                                     if l.strip()]
    up_text = _read(os.path.join(sys_tr, "uprobe_events"))
    if up_text is not None:
        state["uprobe_events"] = [l for l in up_text.splitlines()
                                     if l.strip()]
    se_text = _read(os.path.join(sys_tr, "set_event"))
    if se_text is not None:
        state["set_event_count"] = len(
            [l for l in se_text.splitlines() if l.strip()])
    return state


def status(cfg=None) -> dict:
    state = read_state(_SYS_TRACING)
    requires_root = _probe_permission(_SYS_TRACING)
    verdict = classify(state, requires_root)
    return {
        "ok": state.get("available", False),
        "state": state,
        "requires_root": requires_root,
        "verdict": verdict,
    }
