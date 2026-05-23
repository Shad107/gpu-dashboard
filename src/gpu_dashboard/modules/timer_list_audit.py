"""Module timer_list_audit — kernel timer queue + clocksource
audit (R&D #67.4).

/proc/timer_list dumps every active hrtimer in the system, the
broadcast clock-event device, per-CPU NO_HZ tick-stop state,
and the current clockevent device. /proc/timer_stats existed
on older kernels (≤ 4.9) and exposed the same data with a
counters layout — both are root-only on modern kernels.

Pairs with /sys/devices/system/clocksource/clocksource0/
which is *world-readable* and gives the current/available
clocksources, so even unprivileged we get a partial picture.

Why on a homelab :

* A non-ideal clocksource (`jiffies`, `acpi_pm`) under a busy
  CUDA workload silently inflates every `cudaEventRecord` cost.
* On VMs (Proxmox, KVM) `kvm-clock` is the right choice ; if
  something kicks the host back to `tsc` on a non-monotonic
  TSC, time-related bugs cascade.
* `Broadcast device: bc_set_dev` lines disappearing usually
  means the lapic-deadline timer broke and the system fell
  back to a slower path — pathological tail latency.

Reads :
  /proc/timer_list                            (0400 root)
  /proc/timer_stats                            (kernels ≤ 4.9)
  /sys/devices/system/clocksource/clocksource0/
      {current_clocksource,available_clocksource,
       unbind_clocksource}

Verdicts (priority order) :
  nohz_disabled_on_idle_cpu  /proc/timer_list shows a CPU with
                              tick_stopped: 0 despite a long
                              idle.
  broadcast_device_missing   "Broadcast device:" stanza absent
                              in /proc/timer_list.
  hrtimer_runaway            ≥ 10 000 active hrtimers system-
                              wide.
  requires_root              /proc/timer_list present but
                              unreadable.
  ok                         current_clocksource sane, nothing
                              alarming when readable.
  unknown                    /proc/timer_list absent (test).

stdlib only.
"""
from __future__ import annotations

import os
import re
from typing import List, Optional


NAME = "timer_list_audit"


_PROC_TIMER_LIST = "/proc/timer_list"
_PROC_TIMER_STATS = "/proc/timer_stats"
_SYS_CLOCKSOURCE = "/sys/devices/system/clocksource/clocksource0"

_HRTIMER_RUNAWAY = 10_000


_SANE_CLOCKSOURCES = ("tsc", "kvm-clock", "hyperv_clocksource_tsc_page",
                          "arch_sys_counter")


def _read(path: str) -> Optional[str]:
    try:
        with open(path) as f:
            return f.read()
    except OSError:
        return None


def read_timer_list(path: str = _PROC_TIMER_LIST) -> dict:
    """Returns dict with parsed counts/flags or eacces flag."""
    out = {"present": False, "eacces": False,
              "active_hrtimers": 0,
              "broadcast_device_seen": False,
              "tick_stopped_zero_count": 0,
              "cpus_seen": 0}
    if not os.path.exists(path):
        return out
    out["present"] = True
    try:
        with open(path) as f:
            text = f.read()
    except PermissionError:
        out["eacces"] = True
        return out
    except OSError:
        out["eacces"] = True
        return out

    # Active hrtimer entries are formatted as " #N: <addr>, fn, …"
    # so anchor on the "#" + ": <" prefix.
    for ln in text.splitlines():
        stripped = ln.lstrip()
        if stripped.startswith("#") and ": <" in stripped:
            out["active_hrtimers"] += 1
        if stripped.startswith("Broadcast device"):
            out["broadcast_device_seen"] = True
        if stripped.startswith("cpu:"):
            out["cpus_seen"] += 1
        if "tick_stopped" in ln and ln.strip().endswith(": 0"):
            out["tick_stopped_zero_count"] += 1
    return out


def read_timer_stats(path: str = _PROC_TIMER_STATS) -> dict:
    out = {"present": False, "eacces": False}
    if not os.path.exists(path):
        return out
    out["present"] = True
    try:
        with open(path) as f:
            f.read(64)
    except PermissionError:
        out["eacces"] = True
    except OSError:
        out["eacces"] = True
    return out


def read_clocksource(sys_path: str = _SYS_CLOCKSOURCE) -> dict:
    cur = _read(os.path.join(sys_path, "current_clocksource"))
    avail = _read(os.path.join(sys_path, "available_clocksource"))
    return {
        "current": (cur or "").strip() or None,
        "available": [s for s in (avail or "").split() if s],
    }


def classify(timer_list: dict, timer_stats: dict,
              clocksource: dict, sys_clocksource_present: bool
              ) -> dict:
    if not timer_list["present"] and not sys_clocksource_present:
        return {"verdict": "unknown",
                "reason": ("Neither /proc/timer_list nor "
                          "/sys/devices/system/clocksource "
                          "present — non-Linux or kernel rebuilt "
                          "without these surfaces."),
                "recommendation": ""}

    # If we have clocksource state, check it first regardless of
    # timer_list access.
    cur = clocksource.get("current")
    if cur and cur not in _SANE_CLOCKSOURCES:
        # Insane clocksource is a real issue but its proper place
        # is the existing clocksource_audit. We don't duplicate
        # here — just continue, the audit focuses on timer_list.
        pass

    # Permission gate on timer_list — only check timer-related
    # signals when we can read it.
    if timer_list["present"] and timer_list["eacces"]:
        return {"verdict": "requires_root",
                "reason": ("/proc/timer_list is 0400 root-only ; "
                          "running unprivileged so per-CPU NO_HZ "
                          "/ broadcast / hrtimer state is "
                          "hidden."),
                "recommendation": _recipe_requires_root()}

    if timer_list["present"]:
        # 1) nohz_disabled_on_idle_cpu
        if timer_list["tick_stopped_zero_count"] > 0:
            return {"verdict": "nohz_disabled_on_idle_cpu",
                    "reason": (f"{timer_list['tick_stopped_zero_count']}"
                              f" CPU(s) report tick_stopped: 0 "
                              f"— NO_HZ idle is not active."),
                    "recommendation": _recipe_nohz_idle()}

        # 2) broadcast_device_missing
        if (timer_list["cpus_seen"] > 0
                and not timer_list["broadcast_device_seen"]):
            return {"verdict": "broadcast_device_missing",
                    "reason": ("'Broadcast device:' stanza absent "
                              "from /proc/timer_list — system "
                              "may have fallen back to a slow "
                              "tick path."),
                    "recommendation": _recipe_broadcast_missing()}

        # 3) hrtimer_runaway
        if timer_list["active_hrtimers"] >= _HRTIMER_RUNAWAY:
            return {"verdict": "hrtimer_runaway",
                    "reason": (f"{timer_list['active_hrtimers']:,}"
                              f" active hrtimers — far above "
                              f"typical desktop range."),
                    "recommendation": _recipe_hrtimer_runaway()}

    return {"verdict": "ok",
            "reason": (f"clocksource={cur or '?'} ; "
                      f"available={','.join(clocksource.get('available') or [])} ; "
                      f"active_hrtimers="
                      f"{timer_list['active_hrtimers']}."),
            "recommendation": ""}


def status(config=None,
            proc_timer_list: str = _PROC_TIMER_LIST,
            proc_timer_stats: str = _PROC_TIMER_STATS,
            sys_clocksource: str = _SYS_CLOCKSOURCE) -> dict:
    tl = read_timer_list(proc_timer_list)
    ts = read_timer_stats(proc_timer_stats)
    cs = read_clocksource(sys_clocksource)
    cs_present = os.path.isdir(sys_clocksource)
    verdict = classify(tl, ts, cs, cs_present)
    return {"ok": tl["present"] or cs_present,
              "timer_list_present": tl["present"],
              "timer_list_permission_denied": tl["eacces"],
              "active_hrtimers": tl["active_hrtimers"],
              "broadcast_device_seen": tl["broadcast_device_seen"],
              "tick_stopped_zero_count": tl["tick_stopped_zero_count"],
              "cpus_seen": tl["cpus_seen"],
              "timer_stats_present": ts["present"],
              "clocksource_current": cs.get("current"),
              "clocksource_available": cs.get("available", []),
              "verdict": verdict}


# ── recovery recipes ────────────────────────────────────────────

def _recipe_requires_root() -> str:
    return ("# /proc/timer_list is 0400. To inspect from root :\n"
            "sudo head -200 /proc/timer_list\n"
            "# Count active hrtimers :\n"
            "sudo grep -c ' active ' /proc/timer_list\n"
            "# Show broadcast device :\n"
            "sudo grep -A3 'Broadcast device' /proc/timer_list\n")


def _recipe_nohz_idle() -> str:
    return ("# NO_HZ idle off on some CPU(s). Common causes :\n"
            "#  - 'nohz=off' on kernel cmdline\n"
            "#  - CONFIG_NO_HZ_IDLE not set\n"
            "#  - RT workload pinning the CPU\n"
            "cat /proc/cmdline | tr ' ' '\\n' | grep -i nohz\n"
            "grep CONFIG_NO_HZ /boot/config-$(uname -r)\n")


def _recipe_broadcast_missing() -> str:
    return ("# Missing broadcast clock-event device. Check :\n"
            "sudo dmesg | grep -i 'broadcast\\|lapic-deadline'\n"
            "# Compare expected vs observed :\n"
            "sudo cat /proc/timer_list | grep -A3 -i broadcast\n")


def _recipe_hrtimer_runaway() -> str:
    return ("# Too many active hrtimers — find heavy callers :\n"
            "sudo grep '<.*>' /proc/timer_list | awk '{print $NF}'"
            " | sort | uniq -c | sort -nr | head\n"
            "# Disable timer-heavy modules if they're idle\n"
            "# debug paths (e.g. ftrace, BPF observability).\n")
