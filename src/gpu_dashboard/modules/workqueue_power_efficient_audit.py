"""Module workqueue_power_efficient_audit — wq.power_efficient
+ cpu_intensive_thresh posture (R&D #100.1).

The kernel workqueue subsystem has a `power_efficient` mode
that collapses unbound WQs onto a tiny "power-efficient" CPU
subset. Designed for laptops; when the laptop default leaks
onto a desktop (Ubuntu builds enable it by default on many
hardware classes), GPU submit paths, dm/md callbacks and NVMe
completion all bunch onto a couple of housekeeping cores —
tens of ms tail latency.

Reads :

  /sys/module/workqueue/parameters/power_efficient
  /sys/module/workqueue/parameters/cpu_intensive_thresh_us
  /sys/module/workqueue/parameters/default_affinity_scope
  /proc/cmdline                     (cross-check explicit boot)

The existing `workqueue_cpumask_audit` reads only
/sys/devices/virtual/workqueue/cpumask* (allowed-CPU masks),
not the module parameters. cmdline_audit doesn't key on
`workqueue.*`.

Verdicts (worst-first) :

  wq_power_efficient_forced_on_desktop   err     cmdline forces
                                                 power_efficient=Y
                                                 on a desktop —
                                                 latency hazard.
  wq_cpu_intensive_thresh_too_low        warn    cpu_intensive_
                                                 thresh_us < 5000
                                                 churns the marker
                                                 needlessly.
  wq_power_efficient_runtime_on          accent  power_efficient=Y
                                                 at runtime without
                                                 explicit cmdline
                                                 (silent distro
                                                 default).
  ok                                              wq defaults sane.
  requires_root                                   /sys/module/wq/
                                                  unreadable.
  unknown                                         workqueue sysfs
                                                  absent.

stdlib only.
"""
from __future__ import annotations

import os
from typing import Optional

NAME = "workqueue_power_efficient_audit"

DEFAULT_WQ_SYSFS = "/sys/module/workqueue/parameters"
DEFAULT_CMDLINE = "/proc/cmdline"

_CPU_INTENSIVE_THRESH_MIN_US = 5000


def _read_text(path: str) -> Optional[str]:
    try:
        with open(path, "r", encoding="utf-8") as fh:
            return fh.read()
    except (OSError, PermissionError):
        return None


def _read_str(path: str) -> Optional[str]:
    t = _read_text(path)
    return t.strip() if t is not None else None


def _read_int(path: str) -> Optional[int]:
    t = _read_text(path)
    if t is None:
        return None
    try:
        return int(t.strip())
    except ValueError:
        return None


def parse_cmdline_workqueue(cmdline: Optional[str]) -> dict:
    """Return {power_efficient: 'Y'/'N'} parsed from cmdline."""
    out: dict = {}
    if not cmdline:
        return out
    for tok in cmdline.split():
        if tok.startswith("workqueue.power_efficient="):
            out["power_efficient"] = tok.split("=", 1)[1]
    return out


def classify(sysfs_present: bool,
             power_efficient: Optional[str],
             cpu_intensive_us: Optional[int],
             cmdline_value: Optional[str]) -> dict:
    if not sysfs_present:
        return {"verdict": "unknown",
                "reason": (
                    "/sys/module/workqueue/parameters absent.")}
    if power_efficient is None and cpu_intensive_us is None:
        return {"verdict": "requires_root",
                "reason": (
                    "wq params unreadable — re-run as root.")}

    # err — explicit cmdline force on
    if (cmdline_value
            and cmdline_value.upper() in ("Y", "1", "TRUE")
            and power_efficient
            and power_efficient.upper() == "Y"):
        return {
            "verdict": "wq_power_efficient_forced_on_desktop",
            "reason": (
                f"workqueue.power_efficient={cmdline_value} "
                "on cmdline forces unbound WQs onto power-"
                "efficient CPUs. Adds tens-of-ms tail "
                "latency on GPU submit / dm-md / NVMe paths.")}

    # warn — cpu_intensive threshold too low
    if (cpu_intensive_us is not None
            and 0 < cpu_intensive_us
                  < _CPU_INTENSIVE_THRESH_MIN_US):
        return {
            "verdict": "wq_cpu_intensive_thresh_too_low",
            "reason": (
                f"cpu_intensive_thresh_us="
                f"{cpu_intensive_us} (< "
                f"{_CPU_INTENSIVE_THRESH_MIN_US}) — workqueue "
                "marks tasks cpu-intensive too eagerly, "
                "churning the scheduler hint.")}

    # accent — runtime on without cmdline (silent distro choice)
    if (power_efficient
            and power_efficient.upper() == "Y"
            and not cmdline_value):
        return {
            "verdict": "wq_power_efficient_runtime_on",
            "reason": (
                "workqueue.power_efficient=Y set at runtime "
                "without an explicit cmdline opt-in. Likely "
                "a distro default ; verify it's intentional "
                "on a desktop / homelab class machine.")}

    return {"verdict": "ok",
            "reason": (
                f"power_efficient={power_efficient} ; "
                f"cpu_intensive_thresh_us={cpu_intensive_us}"
                " — defaults sane.")}


def status(config: Optional[dict] = None,
           sysfs: str = DEFAULT_WQ_SYSFS,
           cmdline_path: str = DEFAULT_CMDLINE) -> dict:
    sysfs_present = os.path.isdir(sysfs)
    power_efficient = (
        _read_str(os.path.join(sysfs, "power_efficient"))
        if sysfs_present else None)
    cpu_intensive_us = (
        _read_int(os.path.join(sysfs, "cpu_intensive_thresh_us"))
        if sysfs_present else None)
    affinity_scope = (
        _read_str(os.path.join(sysfs, "default_affinity_scope"))
        if sysfs_present else None)
    cmdline_kv = parse_cmdline_workqueue(
        _read_text(cmdline_path))
    cmdline_value = cmdline_kv.get("power_efficient")

    verdict = classify(sysfs_present, power_efficient,
                       cpu_intensive_us, cmdline_value)
    return {
        "ok": verdict["verdict"] == "ok",
        "power_efficient": power_efficient,
        "cpu_intensive_thresh_us": cpu_intensive_us,
        "default_affinity_scope": affinity_scope,
        "cmdline_power_efficient": cmdline_value,
        "verdict": verdict,
    }
