"""Module timer_migration_nohz_drift_audit — timer_migration ×
nohz_full × rcu_nocbs cross-correlation (R&D #88.4).

cpu_isolation_audit (R&D #?.?) already owns the nohz_full vs
isolcpus / offline alignment axis. This audit owns three
DIFFERENT signals that no existing module checks :

  1. nohz_full is populated AND
     /proc/sys/kernel/timer_migration = 1.
     The kernel will happily migrate timers onto isolated
     CPUs, defeating the whole point of nohz_full. Classic
     realtime / audio / LLM-inference jitter footgun.

  2. /proc/sys/kernel/timer_migration = 0 with NO nohz_full,
     NO isolcpus, NO rcu_nocbs. Pointless overhead — every
     scheduler decision pays the no-migration cost for no
     latency win.

  3. cmdline rcu_nocbs= and cmdline nohz_full= masks DIFFER.
     The operator only half-isolated those CPUs — RCU
     callbacks will still run on them, kicking the
     scheduler.

Reads :

  /proc/sys/kernel/timer_migration          0 | 1
  /sys/devices/system/cpu/nohz_full         CPU list / (null)
  /sys/devices/system/cpu/isolated          CPU list
  /sys/devices/system/cpu/online            CPU list
  /proc/cmdline                             nohz_full=, rcu_nocbs=

Verdicts (worst-first) :

  nohz_full_without_timer_migration_off  err
  timer_migration_off_no_isolation       warn
  rcu_nocbs_mismatch_nohz_full           warn
  aligned                                ok
  requires_root                          timer_migration
                                         unreadable.
  unknown                                no /proc/sys/kernel
                                         present.

stdlib only.
"""
from __future__ import annotations

import os
import re
from typing import Optional, Set

NAME = "timer_migration_nohz_drift_audit"

DEFAULT_PROC_SYS = "/proc/sys"
DEFAULT_SYS_CPU = "/sys/devices/system/cpu"
DEFAULT_PROC_CMDLINE = "/proc/cmdline"

_NOHZ_RE = re.compile(r"\bnohz_full=([^\s]+)")
_NOCBS_RE = re.compile(r"\brcu_nocbs=([^\s]+)")


def _read_text(path: str) -> Optional[str]:
    try:
        with open(path, "r", encoding="utf-8") as fh:
            return fh.read()
    except (OSError, PermissionError):
        return None


def parse_cpu_list(text: Optional[str]) -> Set[int]:
    """Parse '0-3,8,10-11' style list. Tolerant of '(null)'."""
    if not text:
        return set()
    t = text.strip()
    if not t or t == "(null)":
        return set()
    out: Set[int] = set()
    for tok in t.split(","):
        tok = tok.strip()
        if not tok:
            continue
        if "-" in tok:
            try:
                lo, hi = tok.split("-", 1)
                lo_i, hi_i = int(lo), int(hi)
            except ValueError:
                continue
            if lo_i <= hi_i:
                out.update(range(lo_i, hi_i + 1))
        else:
            try:
                out.add(int(tok))
            except ValueError:
                continue
    return out


def read_state(proc_sys: str, sys_cpu: str,
               proc_cmdline: str) -> dict:
    """Return raw state."""
    tm_raw = _read_text(
        os.path.join(proc_sys, "kernel", "timer_migration"))
    nohz_raw = _read_text(os.path.join(sys_cpu, "nohz_full"))
    iso_raw = _read_text(os.path.join(sys_cpu, "isolated"))
    online_raw = _read_text(os.path.join(sys_cpu, "online"))
    cmdline_raw = _read_text(proc_cmdline) or ""

    timer_migration: Optional[int] = None
    if tm_raw is not None:
        try:
            timer_migration = int(tm_raw.strip())
        except ValueError:
            timer_migration = None

    cmdline_nohz: Set[int] = set()
    m = _NOHZ_RE.search(cmdline_raw)
    if m:
        cmdline_nohz = parse_cpu_list(m.group(1))
    cmdline_nocbs: Set[int] = set()
    m = _NOCBS_RE.search(cmdline_raw)
    if m:
        cmdline_nocbs = parse_cpu_list(m.group(1))

    return {
        "timer_migration": timer_migration,
        "tm_readable": tm_raw is not None,
        "nohz_full": parse_cpu_list(nohz_raw),
        "isolated": parse_cpu_list(iso_raw),
        "online": parse_cpu_list(online_raw),
        "cmdline_nohz_full": cmdline_nohz,
        "cmdline_rcu_nocbs": cmdline_nocbs,
    }


def classify(state: dict) -> dict:
    tm = state["timer_migration"]
    if tm is None:
        if not state["tm_readable"]:
            return {"verdict": "requires_root",
                    "reason": (
                        "/proc/sys/kernel/timer_migration "
                        "unreadable — re-run with root.")}
        return {"verdict": "unknown",
                "reason": (
                    "/proc/sys/kernel/timer_migration "
                    "absent — kernel may be too old or this "
                    "build lacks the knob.")}

    nohz = state["nohz_full"]
    iso = state["isolated"]
    cmdline_nohz = state["cmdline_nohz_full"]
    cmdline_nocbs = state["cmdline_rcu_nocbs"]

    # err — nohz_full populated AND timer_migration NOT off
    if nohz and tm == 1:
        return {
            "verdict": "nohz_full_without_timer_migration_off",
            "reason": (
                f"nohz_full = {sorted(nohz)} but "
                "timer_migration = 1 — kernel will migrate "
                "timers onto isolated CPUs, defeating the "
                "purpose. Set: echo 0 > "
                "/proc/sys/kernel/timer_migration"),
            "timer_migration": tm,
            "nohz_full": sorted(nohz),
        }

    # warn — timer_migration=0 with no isolation surface at all
    if (tm == 0 and not nohz and not iso
            and not cmdline_nocbs):
        return {
            "verdict": "timer_migration_off_no_isolation",
            "reason": (
                "timer_migration = 0 but no nohz_full / "
                "isolcpus / rcu_nocbs — every scheduler "
                "decision pays the no-migration cost for no "
                "latency benefit. Restore default: echo 1 > "
                "/proc/sys/kernel/timer_migration"),
            "timer_migration": tm,
        }

    # warn — cmdline rcu_nocbs and nohz_full disagree
    if (cmdline_nohz and cmdline_nocbs
            and cmdline_nohz != cmdline_nocbs):
        return {
            "verdict": "rcu_nocbs_mismatch_nohz_full",
            "reason": (
                f"cmdline nohz_full = {sorted(cmdline_nohz)} "
                "but cmdline rcu_nocbs = "
                f"{sorted(cmdline_nocbs)} — half-isolated, "
                "RCU callbacks still kick the scheduler on "
                "CPUs you meant to keep quiet."),
            "cmdline_nohz_full": sorted(cmdline_nohz),
            "cmdline_rcu_nocbs": sorted(cmdline_nocbs),
        }

    return {"verdict": "aligned",
            "reason": (
                f"timer_migration = {tm} ; "
                f"nohz_full = {sorted(nohz) or 'none'} ; "
                f"rcu_nocbs cmdline = "
                f"{sorted(cmdline_nocbs) or 'none'} — "
                "scheduler / nohz / RCU surface coherent.")}


def status(config: Optional[dict] = None,
           proc_sys: str = DEFAULT_PROC_SYS,
           sys_cpu: str = DEFAULT_SYS_CPU,
           proc_cmdline: str = DEFAULT_PROC_CMDLINE) -> dict:
    state = read_state(proc_sys, sys_cpu, proc_cmdline)
    verdict = classify(state)
    return {
        "ok": verdict["verdict"] == "aligned",
        "timer_migration": state["timer_migration"],
        "nohz_full": sorted(state["nohz_full"]),
        "isolated": sorted(state["isolated"]),
        "cmdline_nohz_full": sorted(state["cmdline_nohz_full"]),
        "cmdline_rcu_nocbs": sorted(state["cmdline_rcu_nocbs"]),
        "verdict": verdict,
    }
