"""Module tracing_events_enable_audit — per-subsystem ftrace
events enable walker (R&D #72.3).

Existing ftrace_audit reads /sys/kernel/tracing/set_event (the
aggregated active-events list). This audit walks the
events/<subsys>/<evt>/enable tree to surface :

  * Which event subsystems are present at all (gpu / drm /
    nvidia / nouveau / i915 / irq / sched / power, etc.).
  * Per-event "stuck on" state (someone left `echo 1 > enable`
    on after a debug session — quietly racks up runtime
    overhead).

The `enable` file is 0640 (group `tracing`) on most kernels —
when run as an unprivileged user, the audit reports
`requires_root` and still surfaces the subsystem inventory
from directory listings.

Verdicts (priority order) :
  gpu_event_stuck_on        ≥1 event under {drm, nvidia,
                              i915, nouveau, virtio_gpu, msm,
                              panfrost} has enable = 1.
  many_subsys_enabled       ≥5 distinct subsystems with at
                              least one enabled event.
  single_evt_enabled        ≥1 event enabled (any subsystem).
  requires_root             /sys/kernel/tracing/events present
                              but enable files unreadable.
  ok                        nothing enabled.
  unknown                   /sys/kernel/tracing/events absent.

stdlib only.
"""
from __future__ import annotations

import os
from typing import Dict, List, Optional, Set


NAME = "tracing_events_enable_audit"


_SYS_TRACING_EVENTS = "/sys/kernel/tracing/events"


_GPU_SUBSYSTEMS = {
    "drm", "nvidia", "i915", "xe", "nouveau", "amdgpu",
    "radeon", "virtio_gpu", "msm", "panfrost", "lima", "v3d",
}


def _read(path: str) -> Optional[str]:
    try:
        with open(path) as f:
            return f.read().strip()
    except OSError:
        return None


def _read_enable(path: str) -> Optional[int]:
    t = _read(path)
    if t is None:
        return None
    t = t.strip()
    if t in ("0", "1"):
        try:
            return int(t)
        except ValueError:
            return None
    # Subsystem-aggregate enable returns "X" when child events
    # disagree.
    if t == "X":
        return -1   # sentinel: mixed
    return None


def list_subsystems(sys_path: str = _SYS_TRACING_EVENTS
                          ) -> List[str]:
    if not os.path.isdir(sys_path):
        return []
    try:
        names = sorted(os.listdir(sys_path))
    except OSError:
        return []
    out: List[str] = []
    for n in names:
        d = os.path.join(sys_path, n)
        if os.path.isdir(d):
            out.append(n)
    return out


def list_subsystem_events(sys_path: str, subsys: str
                              ) -> List[str]:
    d = os.path.join(sys_path, subsys)
    if not os.path.isdir(d):
        return []
    try:
        names = sorted(os.listdir(d))
    except OSError:
        return []
    out: List[str] = []
    for n in names:
        evt_dir = os.path.join(d, n)
        if (os.path.isdir(evt_dir)
                and os.path.isfile(os.path.join(
                    evt_dir, "enable"))):
            out.append(n)
    return out


def scan_enabled(sys_path: str = _SYS_TRACING_EVENTS
                      ) -> dict:
    """Returns {readable, enabled_by_subsys, total_enabled,
    eacces_count}."""
    out = {"readable": False,
              "enabled_by_subsys": {},
              "total_enabled": 0,
              "eacces_count": 0}
    subsystems = list_subsystems(sys_path)
    if not subsystems:
        return out
    by_subsys: Dict[str, List[str]] = {}
    eacces = 0
    any_readable = False
    for s in subsystems:
        # Try the aggregate enable first — if readable + non-X,
        # we don't need to walk every event.
        agg = _read_enable(os.path.join(sys_path, s, "enable"))
        if agg is not None:
            any_readable = True
            if agg == 0:
                # All events in subsys disabled → skip walk.
                continue
            # Agg=1 (all-on) or -1 (mixed) → walk to enumerate.
        else:
            eacces += 1
            continue
        events = list_subsystem_events(sys_path, s)
        for e in events:
            v = _read_enable(
                os.path.join(sys_path, s, e, "enable"))
            if v == 1:
                by_subsys.setdefault(s, []).append(e)
    out["readable"] = any_readable
    out["enabled_by_subsys"] = by_subsys
    out["total_enabled"] = sum(len(v)
                                       for v in by_subsys.values())
    out["eacces_count"] = eacces
    return out


def classify(present: bool,
              subsystems: List[str],
              scan: dict) -> dict:
    if not present:
        return {"verdict": "unknown",
                "reason": ("/sys/kernel/tracing/events absent — "
                          "ftrace not mounted or CONFIG_FTRACE "
                          "not built."),
                "recommendation": ""}

    if not subsystems:
        return {"verdict": "unknown",
                "reason": ("/sys/kernel/tracing/events present "
                          "but no subsystems listable."),
                "recommendation": ""}

    if not scan["readable"]:
        return {"verdict": "requires_root",
                "reason": (f"{len(subsystems)} ftrace event "
                          f"subsystems present, all enable "
                          f"files unreadable (need root or "
                          f"'tracing' group). Cannot detect "
                          f"stuck-on events."),
                "recommendation": _recipe_requires_root()}

    enabled = scan["enabled_by_subsys"]

    # 1) gpu_event_stuck_on
    gpu_subs = [s for s in enabled
                    if s in _GPU_SUBSYSTEMS]
    if gpu_subs:
        sample = ", ".join(
            f"{s}: {','.join(enabled[s][:2])}"
                for s in gpu_subs[:3])
        return {"verdict": "gpu_event_stuck_on",
                "reason": (f"{sum(len(enabled[s]) for s in gpu_subs)}"
                          f" GPU-tracing event(s) left enabled : "
                          f"{sample}."),
                "recommendation": _recipe_gpu_stuck()}

    # 2) many_subsys_enabled
    if len(enabled) >= 5:
        sample = ", ".join(sorted(enabled.keys())[:5])
        return {"verdict": "many_subsys_enabled",
                "reason": (f"{len(enabled)} ftrace subsystems "
                          f"have at least one enabled event : "
                          f"{sample}."),
                "recommendation": _recipe_many_enabled()}

    # 3) single_evt_enabled
    if scan["total_enabled"] > 0:
        sample = ", ".join(
            f"{s}: {','.join(enabled[s][:2])}"
                for s in list(enabled.keys())[:3])
        return {"verdict": "single_evt_enabled",
                "reason": (f"{scan['total_enabled']} ftrace "
                          f"event(s) enabled : {sample}."),
                "recommendation": _recipe_single_enabled()}

    return {"verdict": "ok",
            "reason": (f"{len(subsystems)} subsystems scanned, "
                      f"no events enabled."),
            "recommendation": ""}


def status(config=None,
            sys_path: str = _SYS_TRACING_EVENTS) -> dict:
    present = os.path.isdir(sys_path)
    subsystems = list_subsystems(sys_path)
    scan = scan_enabled(sys_path)
    verdict = classify(present, subsystems, scan)

    gpu_subs_present = sorted(
        s for s in subsystems if s in _GPU_SUBSYSTEMS)

    return {"ok": present,
              "present": present,
              "subsystem_count": len(subsystems),
              "subsystems_sample": subsystems[:20],
              "gpu_subsystems_present": gpu_subs_present,
              "readable": scan["readable"],
              "total_enabled": scan["total_enabled"],
              "enabled_by_subsys": scan["enabled_by_subsys"],
              "verdict": verdict}


# ── recovery recipes ────────────────────────────────────────────

def _recipe_requires_root() -> str:
    return ("# ftrace 'enable' files are 0640 root:tracing.\n"
            "# Audit currently-on events as root :\n"
            "sudo grep -lr '^1$' /sys/kernel/tracing/events/\\\n"
            "  */*/enable 2>/dev/null\n"
            "# Or join the 'tracing' group :\n"
            "sudo usermod -aG tracing $USER\n")


def _recipe_gpu_stuck() -> str:
    return ("# A GPU-tracing event was left enabled. Each tick\n"
            "# costs cycles. Disable everything under a subsys :\n"
            "echo 0 | sudo tee /sys/kernel/tracing/events/\\\n"
            "  <subsys>/enable\n"
            "# Or selectively :\n"
            "echo 0 | sudo tee /sys/kernel/tracing/events/\\\n"
            "  drm/drm_vblank_event/enable\n")


def _recipe_many_enabled() -> str:
    return ("# Many subsystems active — likely a debug stack\n"
            "# (Tetragon, bpftrace, perf, sysprof). Clear all :\n"
            "echo 0 | sudo tee /sys/kernel/tracing/events/enable\n"
            "# Or use trace-cmd reset :\n"
            "sudo trace-cmd reset\n")


def _recipe_single_enabled() -> str:
    return ("# A single ftrace event is enabled. Identify :\n"
            "sudo grep -lr '^1$' /sys/kernel/tracing/events/*/\\\n"
            "  */enable 2>/dev/null\n"
            "# Disable globally :\n"
            "echo 0 | sudo tee /sys/kernel/tracing/events/enable\n")
