"""Module devfreq_event_audit — devfreq event counters (R&D #65.4).

Distinct from R&D #62.1 devfreq_audit (which reads
/sys/class/devfreq/ — governor + state). This module covers
the separate /sys/class/devfreq-event/ subsystem : event-counter
PMUs (e.g. exynos-ppmu, rockchip-dfi) that feed `simple_ondemand`
DRAM/memory-bus DVFS governors.

Why this matters on ARM SoCs + some Intel platforms with on-die
DRAM event counters :

* A governor depends on a devfreq-event source — if that source
  has `enable_count = 0`, the governor falls back to a static
  freq, silently. Memory bandwidth scaling stops working.
* Orphan events (registered but no consumer governor) usually
  mean an unbound devfreq driver.

Reads :
  /sys/class/devfreq-event/event*/{name, enable_count}
  (set_event / event_data are usually write-only — we skip them)

Verdicts (priority-ordered) :
  event_disabled               ≥1 event with enable_count = 0
                               (memory-bus PMU not feeding any
                               DVFS governor).
  event_orphaned_governor      Heuristic : the governor
                               `simple_ondemand` is in use on a
                               devfreq device whose feeding event
                               is disabled. (Cross-check tricky in
                               stdlib — surfaced when both are
                               available.)
  class_absent                 /sys/class/devfreq-event absent OR
                               empty.
  ok                           Events all enabled.
  unknown                      Both /sys/class/devfreq-event and
                               /sys/class/devfreq absent.

stdlib only.
"""
from __future__ import annotations

import os
import re
from typing import List, Optional


NAME = "devfreq_event_audit"


_SYS_DEVFREQ_EVENT = "/sys/class/devfreq-event"
_SYS_DEVFREQ = "/sys/class/devfreq"


def _read(p: str) -> Optional[str]:
    try:
        with open(p) as f:
            return f.read().strip()
    except OSError:
        return None


def _read_int(p: str) -> Optional[int]:
    t = _read(p)
    if t is None:
        return None
    try:
        return int(t)
    except ValueError:
        return None


def list_events(sys_devfreq_event: str = _SYS_DEVFREQ_EVENT
                  ) -> List[dict]:
    if not os.path.isdir(sys_devfreq_event):
        return []
    out: List[dict] = []
    for name in sorted(os.listdir(sys_devfreq_event)):
        d = os.path.join(sys_devfreq_event, name)
        if not os.path.isdir(d):
            continue
        out.append({
            "id": name,
            "name": _read(os.path.join(d, "name")),
            "enable_count": _read_int(
                os.path.join(d, "enable_count")),
        })
    return out


def list_devfreq_devices(sys_devfreq: str = _SYS_DEVFREQ
                          ) -> List[dict]:
    if not os.path.isdir(sys_devfreq):
        return []
    out: List[dict] = []
    for name in sorted(os.listdir(sys_devfreq)):
        d = os.path.join(sys_devfreq, name)
        if not os.path.isdir(d):
            continue
        out.append({
            "id": name,
            "governor": _read(os.path.join(d, "governor")),
        })
    return out


def classify(events: List[dict], devfreqs: List[dict],
              event_class_present: bool,
              devfreq_class_present: bool) -> dict:
    if not event_class_present and not devfreq_class_present:
        return {"verdict": "unknown",
                "reason": ("Neither /sys/class/devfreq-event nor "
                          "/sys/class/devfreq present — host has "
                          "no devfreq scaling."),
                "recommendation": ""}

    if not events and event_class_present:
        return {"verdict": "class_absent",
                "reason": ("/sys/class/devfreq-event directory "
                          "present but empty — no event PMUs "
                          "registered."),
                "recommendation": ""}

    if not event_class_present:
        return {"verdict": "class_absent",
                "reason": ("/sys/class/devfreq-event absent — "
                          "kernel without CONFIG_PM_DEVFREQ_EVENT "
                          "or no event PMU driver."),
                "recommendation": ""}

    disabled = [e for e in events
                   if e.get("enable_count") == 0]
    if disabled:
        sample = ", ".join(
            f"{e['name'] or e['id']}"
            for e in disabled[:3])
        # Check if simple_ondemand governor is in use somewhere
        # (heuristic for orphan).
        uses_ondemand = any(
            (d.get("governor") or "").lower() == "simple_ondemand"
            for d in devfreqs)
        if uses_ondemand:
            return {"verdict": "event_orphaned_governor",
                    "reason": (f"{len(disabled)} event PMU(s) "
                              f"disabled while simple_ondemand "
                              f"governor is in use : {sample}. "
                              f"Governor uses a static freq."),
                    "recommendation": _recipe_enable_event()}
        return {"verdict": "event_disabled",
                "reason": (f"{len(disabled)} event PMU(s) with "
                          f"enable_count = 0 : {sample}."),
                "recommendation": _recipe_enable_event()}

    return {"verdict": "ok",
            "reason": (f"{len(events)} event PMU(s), all enabled."),
            "recommendation": ""}


def status(config=None,
            sys_devfreq_event: str = _SYS_DEVFREQ_EVENT,
            sys_devfreq: str = _SYS_DEVFREQ) -> dict:
    event_class_present = os.path.isdir(sys_devfreq_event)
    devfreq_class_present = os.path.isdir(sys_devfreq)
    events = list_events(sys_devfreq_event)
    devfreqs = list_devfreq_devices(sys_devfreq)
    ok = bool(event_class_present or devfreq_class_present)
    verdict = classify(events, devfreqs,
                          event_class_present,
                          devfreq_class_present)
    return {"ok": ok,
              "event_class_present": event_class_present,
              "devfreq_class_present": devfreq_class_present,
              "event_count": len(events),
              "events": events,
              "devfreq_devices": devfreqs,
              "verdict": verdict}


# ── recovery recipes ────────────────────────────────────────────

def _recipe_enable_event() -> str:
    return ("# Inspect the event PMU :\n"
            "for e in /sys/class/devfreq-event/event*; do\n"
            "  echo \"$(cat $e/name) enable_count=$(cat $e/enable_count)\"\n"
            "done\n"
            "# Typically the event PMU is enabled by the governor\n"
            "# on first activation. Verify the devfreq device\n"
            "# linkage in /sys/class/devfreq/<dev>/devfreq-event/\n"
            "# (if exposed) or DT bindings.\n")
