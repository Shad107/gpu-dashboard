"""Module loadavg_pressure_audit — realized load + RT throttle (R&D #57.4).

Distinct from existing :
  * psi_pressure_audit (#53.1) — PSI percentages, threshold-based.
  * sched_audit (#47.4)         — /proc/schedstat per-CPU run_delay.
This module correlates realized loadavg + D-state count + nr_cpus,
and surfaces the RT-throttle safety knob explicitly.

Why this matters :

* loadavg[0] > 2 × nr_cpus with high procs_blocked is a CPU /
  IO bottleneck the user feels (tokens/s drops) but PSI may not
  exceed its 10 % threshold so #53.1 reports OK.
* /proc/sys/kernel/sched_rt_runtime_us = -1 (or > sched_rt_period
  _us) disables the safety throttle entirely. A buggy realtime
  task can then starve every cpu-bound process — including the
  GPU driver IRQ thread.
* Sustained procs_blocked > 3 means uninterruptible-sleep tasks
  are piling up — usually NVMe IO or SMB / NFS hangs.

Reads :
  /proc/loadavg
  /proc/stat                  (procs_running, procs_blocked)
  /proc/cpuinfo               (derive nr_cpus)
  /proc/sys/kernel/sched_rt_runtime_us
  /proc/sys/kernel/sched_rt_period_us

Verdicts (priority-ordered) :
  rt_throttle_disabled         sched_rt_runtime_us = -1 OR
                               > sched_rt_period_us — safety
                               throttle off.
  D_state_storm                procs_blocked > 3 (uninterruptible
                               IO pileup).
  overcommitted                loadavg[0] > 2 × nr_cpus.
  ok                           load and runqueue healthy.
  unknown                      /proc/loadavg absent.

stdlib only.
"""
from __future__ import annotations

import os
from typing import List, Optional, Tuple


NAME = "loadavg_pressure_audit"


_PROC_LOADAVG = "/proc/loadavg"
_PROC_STAT = "/proc/stat"
_PROC_CPUINFO = "/proc/cpuinfo"
_PROC_RT_RUNTIME = "/proc/sys/kernel/sched_rt_runtime_us"
_PROC_RT_PERIOD = "/proc/sys/kernel/sched_rt_period_us"


def _read(p: str) -> Optional[str]:
    try:
        with open(p) as f:
            return f.read()
    except OSError:
        return None


def _read_int(p: str) -> Optional[int]:
    t = _read(p)
    if t is None:
        return None
    try:
        return int(t.strip())
    except ValueError:
        return None


def parse_loadavg(text: Optional[str]
                    ) -> Tuple[Optional[float], Optional[float],
                                Optional[float]]:
    if not text:
        return None, None, None
    parts = text.split()
    if len(parts) < 3:
        return None, None, None
    try:
        return float(parts[0]), float(parts[1]), float(parts[2])
    except ValueError:
        return None, None, None


def parse_stat(text: Optional[str]) -> dict:
    out: dict = {}
    if not text:
        return out
    for line in text.splitlines():
        if line.startswith("procs_running"):
            try:
                out["procs_running"] = int(line.split()[1])
            except (ValueError, IndexError):
                pass
        elif line.startswith("procs_blocked"):
            try:
                out["procs_blocked"] = int(line.split()[1])
            except (ValueError, IndexError):
                pass
    return out


def count_cpus(text: Optional[str]) -> int:
    if not text:
        return 0
    return sum(1 for line in text.splitlines()
                  if line.startswith("processor"))


def classify(loadavg: Tuple[Optional[float], Optional[float],
                              Optional[float]],
              stat: dict,
              nr_cpus: int,
              rt_runtime_us: Optional[int],
              rt_period_us: Optional[int]) -> dict:
    la1, la5, la15 = loadavg
    if la1 is None:
        return {"verdict": "unknown",
                "reason": "/proc/loadavg unreadable.",
                "recommendation": ""}

    # 1) rt_throttle_disabled
    if rt_runtime_us is not None:
        disabled = (rt_runtime_us == -1 or
                       (rt_period_us is not None and
                        rt_period_us > 0 and
                        rt_runtime_us > rt_period_us))
        if disabled:
            return {"verdict": "rt_throttle_disabled",
                    "reason": (f"sched_rt_runtime_us = "
                              f"{rt_runtime_us} "
                              f"(period = {rt_period_us}) — "
                              f"safety throttle off. A runaway "
                              f"RT task can starve every CPU."),
                    "recommendation": _recipe_rt_throttle()}

    # 2) D_state_storm
    blocked = stat.get("procs_blocked", 0)
    if blocked > 3:
        return {"verdict": "D_state_storm",
                "reason": (f"procs_blocked = {blocked} (> 3). "
                          f"Uninterruptible-sleep tasks piling up "
                          f"— usually NVMe / NFS hang."),
                "recommendation": _recipe_d_state()}

    # 3) overcommitted
    if nr_cpus > 0 and la1 > 2 * nr_cpus:
        return {"verdict": "overcommitted",
                "reason": (f"loadavg[0] = {la1:.2f} > 2 × "
                          f"{nr_cpus} CPUs ({2 * nr_cpus}). "
                          f"Runqueue is saturated, tokens/s "
                          f"will drop."),
                "recommendation": _recipe_overcommit()}

    return {"verdict": "ok",
            "reason": (f"loadavg {la1:.2f}/{la5 or 0:.2f}/"
                      f"{la15 or 0:.2f} on {nr_cpus} CPUs ; "
                      f"procs_blocked = {blocked}."),
            "recommendation": ""}


def status(config=None,
            proc_loadavg: str = _PROC_LOADAVG,
            proc_stat: str = _PROC_STAT,
            proc_cpuinfo: str = _PROC_CPUINFO,
            proc_rt_runtime: str = _PROC_RT_RUNTIME,
            proc_rt_period: str = _PROC_RT_PERIOD) -> dict:
    la = parse_loadavg(_read(proc_loadavg))
    stat = parse_stat(_read(proc_stat))
    nr_cpus = count_cpus(_read(proc_cpuinfo))
    rt_runtime = _read_int(proc_rt_runtime)
    rt_period = _read_int(proc_rt_period)
    ok = la[0] is not None
    verdict = classify(la, stat, nr_cpus, rt_runtime, rt_period)
    return {"ok": ok,
              "loadavg_1m": la[0],
              "loadavg_5m": la[1],
              "loadavg_15m": la[2],
              "procs_running": stat.get("procs_running"),
              "procs_blocked": stat.get("procs_blocked"),
              "nr_cpus": nr_cpus,
              "sched_rt_runtime_us": rt_runtime,
              "sched_rt_period_us": rt_period,
              "verdict": verdict}


# ── recovery recipes ────────────────────────────────────────────

def _recipe_rt_throttle() -> str:
    return ("# Restore the RT safety throttle (95 % of period) :\n"
            "echo 950000 | sudo tee /proc/sys/kernel/sched_rt_runtime_us\n"
            "# Persist via /etc/sysctl.d/99-rt-throttle.conf :\n"
            "#   kernel.sched_rt_runtime_us = 950000\n"
            "# Removing the throttle is only safe on a rig with a\n"
            "# carefully bounded RT workload.\n")


def _recipe_d_state() -> str:
    return ("# Identify D-state tasks :\n"
            "ps -eo state,pid,cmd | awk '$1==\"D\"' | head\n"
            "# Common culprits : a hung NVMe submission queue, an\n"
            "# NFS/SMB mount on a slow link, a stuck cifs umount.\n"
            "# As a first step :\n"
            "dmesg --since '5 minutes ago' | grep -iE 'hung|blocked|nfs|nvme'\n")


def _recipe_overcommit() -> str:
    return ("# Find the CPU hogs :\n"
            "top -bn1 -o '%CPU' | head -20\n"
            "# Pin inference to dedicated CPUs via systemd :\n"
            "#   [Service]\n"
            "#   CPUAffinity=0-7\n"
            "# … or numactl --physcpubind=0-7 llama-server …\n")
