"""Module rcu_expedited_audit — RCU expedited / isolation
coherence (R&D #82.3).

RCU (Read-Copy-Update) is the kernel's scalable
synchronisation primitive.  Misconfiguration silently
destroys p99 latency on inference / RT workloads :

  * rcu_expedited = 1 globally fires inter-processor
    interrupts (IPIs) on *every* CPU for grace-period
    starts.  On a box where cores are isolated for low-
    latency work (isolcpus / rcu_nocbs / nohz_full), the
    expedited IPIs nullify the isolation effort.
  * rcu_cpu_stall_timeout < 21 s causes false-positive
    stall splats in journalctl, hiding the real
    stalls under the noise.
  * rcu_nocbs= declared in /proc/cmdline without matching
    isolcpus= / nohz_full= is a half-finished RT setup —
    the no-callbacks CPUs aren't actually shielded.

Reads :
  /sys/kernel/rcu_expedited                  (0/1)
  /sys/kernel/rcu_normal                     (0/1)
  /proc/sys/kernel/rcu_expedited             (alt sysctl)
  /proc/sys/kernel/rcu_normal                (alt sysctl)
  /proc/sys/kernel/rcu_cpu_stall_timeout     (seconds)
  /sys/devices/system/cpu/isolated           ("1-3,5,…")
  /proc/cmdline                              (rcu_nocbs=,
                                              isolcpus=,
                                              nohz_full=)

Verdicts (worst first) :

  rcu_expedited_with_isolation   rcu_expedited = 1  AND
                                 isolated CPUs present —
                                 IPI storms hit isolated
                                 cores, defeating isolation.
  rcu_stall_timeout_short        stall_timeout < 21 s —
                                 false-positive splats
                                 expected.
  rcu_nocbs_no_isolation         rcu_nocbs declared but no
                                 isolcpus / nohz_full —
                                 half-finished RT setup.
  ok                             defaults coherent.
  unknown                        no /sys/kernel/rcu_* and
                                 no /proc/sys/kernel/rcu_*.
"""
from __future__ import annotations

import os
import re
from typing import Optional

DEFAULT_SYS_RCU = "/sys/kernel"
DEFAULT_PROC_KERNEL = "/proc/sys/kernel"
DEFAULT_CPU_ISOLATED = "/sys/devices/system/cpu/isolated"
DEFAULT_CMDLINE = "/proc/cmdline"

# Thresholds
_STALL_TIMEOUT_MIN = 21  # seconds

# Parser for kernel cmdline boot args
_CMD_TOKEN_RE = re.compile(r"(\w+)=([^\s]+)")


def _read_text(path: str) -> Optional[str]:
    try:
        with open(path, "r", encoding="utf-8") as fh:
            return fh.read().strip()
    except (OSError, PermissionError):
        return None


def _read_int(path: str) -> Optional[int]:
    s = _read_text(path)
    if s is None or s == "":
        return None
    try:
        return int(s)
    except ValueError:
        return None


def _parse_cpu_list(text: Optional[str]) -> list[int]:
    """Parse '1-3,5,7-9' → [1,2,3,5,7,8,9]."""
    if not text:
        return []
    out: list[int] = []
    for piece in text.split(","):
        piece = piece.strip()
        if not piece:
            continue
        if "-" in piece:
            try:
                lo, hi = piece.split("-", 1)
                out.extend(range(int(lo), int(hi) + 1))
            except ValueError:
                continue
        else:
            try:
                out.append(int(piece))
            except ValueError:
                continue
    return out


def read_state(sys_root: str = DEFAULT_SYS_RCU,
               proc_root: str = DEFAULT_PROC_KERNEL,
               isolated_path: str = DEFAULT_CPU_ISOLATED,
               cmdline_path: str = DEFAULT_CMDLINE) -> dict:
    """Returns flat dict of RCU + isolation state."""
    expedited_sys = _read_int(
        os.path.join(sys_root, "rcu_expedited"))
    normal_sys = _read_int(
        os.path.join(sys_root, "rcu_normal"))
    expedited_proc = _read_int(
        os.path.join(proc_root, "rcu_expedited"))
    normal_proc = _read_int(
        os.path.join(proc_root, "rcu_normal"))
    stall_timeout = _read_int(
        os.path.join(proc_root, "rcu_cpu_stall_timeout"))
    isolated_text = _read_text(isolated_path)
    cmdline = _read_text(cmdline_path) or ""

    cmd_args: dict[str, str] = {}
    for m in _CMD_TOKEN_RE.finditer(cmdline):
        cmd_args[m.group(1)] = m.group(2)

    return {
        "rcu_expedited": (
            expedited_sys if expedited_sys is not None
            else expedited_proc),
        "rcu_normal": (
            normal_sys if normal_sys is not None
            else normal_proc),
        "rcu_cpu_stall_timeout": stall_timeout,
        "isolated_cpus": _parse_cpu_list(isolated_text),
        "isolcpus_cmd": cmd_args.get("isolcpus"),
        "nohz_full_cmd": cmd_args.get("nohz_full"),
        "rcu_nocbs_cmd": cmd_args.get("rcu_nocbs"),
    }


def classify(state: dict) -> dict:
    # All readable RCU knobs missing → unknown
    have_any = (
        state.get("rcu_expedited") is not None
        or state.get("rcu_normal") is not None
        or state.get("rcu_cpu_stall_timeout") is not None)
    if not have_any:
        return {"verdict": "unknown",
                "reason": (
                    "No /sys/kernel/rcu_* or "
                    "/proc/sys/kernel/rcu_* readable.")}

    isolated = state.get("isolated_cpus", []) or []
    has_isolation = bool(isolated)

    # 1. err — expedited globally + isolation present
    if state.get("rcu_expedited") == 1 and has_isolation:
        return {"verdict": "rcu_expedited_with_isolation",
                "reason": (
                    "rcu_expedited = 1 with isolated CPUs "
                    f"{isolated} — IPI storms on isolated "
                    "cores defeat the isolation effort."),
                "isolated_cpus": isolated}

    # 2. warn — short stall timeout
    stall = state.get("rcu_cpu_stall_timeout")
    if stall is not None and stall < _STALL_TIMEOUT_MIN:
        return {"verdict": "rcu_stall_timeout_short",
                "reason": (
                    f"rcu_cpu_stall_timeout = {stall} s "
                    f"(< {_STALL_TIMEOUT_MIN} s) — false-"
                    "positive stall splats expected."),
                "rcu_cpu_stall_timeout": stall}

    # 3. accent — rcu_nocbs declared but no isolation
    nocbs = state.get("rcu_nocbs_cmd")
    isolcpus = state.get("isolcpus_cmd")
    nohz_full = state.get("nohz_full_cmd")
    if nocbs and not isolcpus and not nohz_full:
        return {"verdict": "rcu_nocbs_no_isolation",
                "reason": (
                    f"rcu_nocbs = {nocbs} on cmdline but "
                    "neither isolcpus nor nohz_full — "
                    "half-finished RT setup."),
                "rcu_nocbs": nocbs}

    return {"verdict": "ok",
            "reason": (
                f"rcu_expedited={state.get('rcu_expedited')} ; "
                f"stall_timeout="
                f"{state.get('rcu_cpu_stall_timeout')} s ; "
                f"{len(isolated)} isolated CPU(s).")}


def status(config: Optional[dict] = None,
           sys_root: str = DEFAULT_SYS_RCU,
           proc_root: str = DEFAULT_PROC_KERNEL,
           isolated_path: str = DEFAULT_CPU_ISOLATED,
           cmdline_path: str = DEFAULT_CMDLINE) -> dict:
    state = read_state(sys_root, proc_root,
                        isolated_path, cmdline_path)
    verdict = classify(state)
    return {
        "ok": verdict["verdict"] not in (
            "unknown",
            "rcu_expedited_with_isolation"),
        "state": state,
        "verdict": verdict,
    }
