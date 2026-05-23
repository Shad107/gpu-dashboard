"""Module cpuidle_residency_audit — per-CPU per-state cpuidle (R&D #65.1).

Distinct from existing cpuidle_audit (which only reads cpu0's
state names + latencies = config). This module walks **every**
CPU's per-state counters :

  state*/{name, latency, disable, residency, time, usage,
            above, below}

`time` is microseconds spent in that state, `usage` is number of
entries, `above` / `below` are heuristic miss counters (governor
picked the wrong direction).

Why this matters on an LLM rig :

* POLL/C1 dominating idle time on a CPU tuned for low-latency
  inference is OK ; on a general-purpose box it wastes ~5-10 W
  vs C6.
* C6 (deep idle) starvation = governor prefers shallower states,
  costing idle power.
* `disable=1` on a single CPU (typically by an OS-X tuner or
  vendor quirk) creates an asymmetric idle profile.
* High `above` count = governor predicted shallower than needed
  (kept the CPU in C1 when C6 was available).

Reads :
  /sys/devices/system/cpu/cpu*/cpuidle/state*/{name, disable,
    residency, time, usage, above, below}

Verdicts (priority-ordered) :
  poll_dominant                POLL (state0) > 80 % of idle time
                               across CPUs.
  c6_starved                   C6/C7-named state present but
                               accumulated time < 5 % of all idle.
  governor_mispredict          Sum(above) > Sum(below) × 5 across
                               all CPUs (governor heavily
                               under-shoots).
  state_disabled_asymmetry     ≥1 CPU has a state with disable=1
                               while peers don't.
  ok                           Idle profile healthy.
  unknown                      /sys/devices/system/cpu/cpu0/cpuidle
                               absent.

stdlib only.
"""
from __future__ import annotations

import os
import re
from typing import Dict, List, Optional


NAME = "cpuidle_residency_audit"


_SYS_CPU = "/sys/devices/system/cpu"

_CPU_DIR_RE = re.compile(r"^cpu(\d+)$")
_STATE_DIR_RE = re.compile(r"^state(\d+)$")


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


def list_cpus(sys_cpu: str = _SYS_CPU) -> List[int]:
    if not os.path.isdir(sys_cpu):
        return []
    out: List[int] = []
    for name in sorted(os.listdir(sys_cpu)):
        m = _CPU_DIR_RE.match(name)
        if not m:
            continue
        idx = int(m.group(1))
        if os.path.isdir(os.path.join(sys_cpu, name, "cpuidle")):
            out.append(idx)
    return out


def read_states_for_cpu(cpu_idx: int,
                           sys_cpu: str = _SYS_CPU) -> List[dict]:
    cidle = os.path.join(sys_cpu, f"cpu{cpu_idx}", "cpuidle")
    if not os.path.isdir(cidle):
        return []
    out: List[dict] = []
    for name in sorted(os.listdir(cidle)):
        m = _STATE_DIR_RE.match(name)
        if not m:
            continue
        d = os.path.join(cidle, name)
        out.append({
            "id": name,
            "idx": int(m.group(1)),
            "name": _read(os.path.join(d, "name")),
            "disable": _read_int(os.path.join(d, "disable")),
            "residency": _read_int(
                os.path.join(d, "residency")),
            "time": _read_int(os.path.join(d, "time")),
            "usage": _read_int(os.path.join(d, "usage")),
            "above": _read_int(os.path.join(d, "above")),
            "below": _read_int(os.path.join(d, "below")),
        })
    return out


def classify(cpu_states: Dict[int, List[dict]]) -> dict:
    if not cpu_states:
        return {"verdict": "unknown",
                "reason": ("No /sys/devices/system/cpu/cpu*/cpuidle "
                          "present — kernel without cpuidle or "
                          "container without sysfs."),
                "recommendation": ""}

    # Aggregate totals.
    poll_time = 0
    c6plus_time = 0
    total_time = 0
    total_above = 0
    total_below = 0
    state_names_seen: List[str] = []
    state_disables: List[tuple] = []  # (cpu, state_name, disable)

    for cpu, states in cpu_states.items():
        for s in states:
            t = s.get("time") or 0
            total_time += t
            name = (s.get("name") or "").upper()
            if "POLL" in name:
                poll_time += t
            elif name.startswith(("C6", "C7", "C8", "C9", "C10")):
                c6plus_time += t
            total_above += s.get("above") or 0
            total_below += s.get("below") or 0
            if name and name not in state_names_seen:
                state_names_seen.append(name)
            if s.get("disable") is not None:
                state_disables.append(
                    (cpu, s.get("name"), s["disable"]))

    # 1) poll_dominant
    if total_time > 0 and poll_time > total_time * 0.80:
        pct = 100 * poll_time / total_time
        return {"verdict": "poll_dominant",
                "reason": (f"POLL state holds {pct:.0f}% of cpu "
                          f"idle time. Wastes 5-10 W vs C6 on "
                          f"general-purpose hosts."),
                "recommendation": _recipe_governor()}

    # 2) c6_starved
    has_c6 = any(s.upper().startswith(("C6", "C7", "C8"))
                    for s in state_names_seen)
    if has_c6 and total_time > 0 and \
            c6plus_time < total_time * 0.05:
        pct = 100 * c6plus_time / total_time
        return {"verdict": "c6_starved",
                "reason": (f"C6+ deep idle holds only {pct:.1f}% "
                          f"of idle time despite being available. "
                          f"Governor parks in shallow states."),
                "recommendation": _recipe_governor()}

    # 3) governor_mispredict
    if total_below > 0 and total_above > total_below * 5:
        return {"verdict": "governor_mispredict",
                "reason": (f"Governor missed deeper-than-predicted "
                          f"idle {total_above} times vs "
                          f"shallower-than-predicted {total_below}. "
                          f"5× skew → re-evaluate governor."),
                "recommendation": _recipe_governor()}

    # 4) state_disabled_asymmetry — find a state name with both
    #    disable=0 and disable=1 on different CPUs.
    by_name: Dict[str, List[int]] = {}
    for cpu, name, dis in state_disables:
        if name is None:
            continue
        by_name.setdefault(name, []).append(dis)
    for name, vals in by_name.items():
        if 0 in vals and 1 in vals:
            return {"verdict": "state_disabled_asymmetry",
                    "reason": (f"State '{name}' is enabled on "
                              f"some CPUs and disabled on others. "
                              f"Idle profile asymmetric."),
                    "recommendation": _recipe_disabled()}

    return {"verdict": "ok",
            "reason": (f"{len(cpu_states)} CPU(s), idle profile "
                      f"balanced."),
            "recommendation": ""}


def status(config=None, sys_cpu: str = _SYS_CPU) -> dict:
    cpus = list_cpus(sys_cpu)
    cpu_states: Dict[int, List[dict]] = {}
    for cpu in cpus:
        cpu_states[cpu] = read_states_for_cpu(cpu, sys_cpu)
    ok = bool(cpus)
    verdict = classify(cpu_states)
    # Build a compact summary instead of dumping every state.
    sample = []
    if cpus:
        first = cpus[0]
        sample = cpu_states.get(first, [])
    return {"ok": ok,
              "cpu_count": len(cpus),
              "state_count_per_cpu": (
                  len(sample) if sample else 0),
              "sample_cpu_states": sample,
              "verdict": verdict}


# ── recovery recipes ────────────────────────────────────────────

def _recipe_governor() -> str:
    return ("# Switch cpuidle governor to 'menu' (heuristic-aware)\n"
            "# or 'teo' (timer events oriented) :\n"
            "echo menu | sudo tee /sys/devices/system/cpu/cpuidle/current_governor\n"
            "# Verify deeper-state residency :\n"
            "grep . /sys/devices/system/cpu/cpu0/cpuidle/state*/{name,time}\n")


def _recipe_disabled() -> str:
    return ("# Find which CPU has an asymmetrically disabled state :\n"
            "for c in /sys/devices/system/cpu/cpu*/cpuidle/state*/disable; do\n"
            "  v=$(cat $c)\n"
            "  [ \"$v\" = 1 ] && echo \"DISABLED: $c\"\n"
            "done\n"
            "# Re-enable :\n"
            "echo 0 | sudo tee /sys/devices/system/cpu/cpu*/cpuidle/state*/disable\n")
