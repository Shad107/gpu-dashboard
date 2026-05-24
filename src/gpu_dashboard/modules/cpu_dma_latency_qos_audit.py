"""Module cpu_dma_latency_qos_audit — CPU PM-QoS resume
latency + /dev/cpu_dma_latency holder scan (R&D #82.2).

The classic homelab "why is my idle wattage 90 W instead of
35 W" leak is caused by something pinning the CPU PM-QoS
constraint :

  * a userspace daemon (PulseAudio, Steam, an old
    wireplumber, sometimes Discord) keeps an open file
    descriptor on /dev/cpu_dma_latency writing the value 0
    → kernel blocks every deep C-state → fan ramps + 30 W
    idle leak,
  * or per-CPU pm_qos_resume_latency_us is clamped to a
    small non-zero value via sysfs → similar effect.

Reads :

  /sys/devices/system/cpu/cpu*/power/pm_qos_resume_latency_us
       0 = NO_CONSTRAINT (deep C-states allowed) — the
       default and healthy value.  Any non-zero value
       restricts the maximum acceptable resume latency.
  /sys/devices/system/cpu/cpu*/cpuidle/state*/{name,disable,residency}
       presence inventory ; non-existent on VMs / minimal
       kernels with no idle states surfaced.
  /proc/<pid>/fd/*
       readlink-targets matching /dev/cpu_dma_latency tell
       us which processes are holding the PM-QoS handle —
       we can not read the value they wrote without
       opening the device ourselves (which would itself
       impose a constraint), but the open handle alone is
       a strong signal.

Verdicts (worst first) :

  pm_qos_latency_clamped_majority   non-zero
                                    pm_qos_resume_latency_us
                                    on > 50 % of CPUs — deep
                                    C-states unreachable.
  cpu_dma_latency_held_external     a non-systemd / non-
                                    sandbox process holds an
                                    open handle on
                                    /dev/cpu_dma_latency.
  pm_qos_mixed                      some CPUs clamped, some
                                    unconstrained — accidental
                                    cpu-shielding leftover.
  no_cpuidle                        /sys/.../cpuidle absent on
                                    every CPU — VM or BIOS
                                    disabled idle states.
  ok                                all CPUs at NO_CONSTRAINT,
                                    no external holders.
  requires_root                     could not enumerate
                                    /proc/<pid>/fd at all.
  unknown                           /sys/.../pm_qos_resume_*
                                    missing on every CPU.
"""
from __future__ import annotations

import os
import re
from typing import Optional

DEFAULT_CPU_ROOT = "/sys/devices/system/cpu"
DEFAULT_PROC_ROOT = "/proc"
CPU_DMA_LATENCY_DEV = "/dev/cpu_dma_latency"

# Process names that legitimately hold /dev/cpu_dma_latency.
# systemd and CPU-governor daemons are expected to keep an
# open handle — they are not the leak we are after.
_BENIGN_HOLDER_COMMS = frozenset({
    "systemd", "systemd-logind", "systemd-oomd",
    "thermald", "ananicy", "tlp", "tuned",
    "power-profiles-daemon",
})


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


def list_cpu_dirs(root: str = DEFAULT_CPU_ROOT) -> list[str]:
    try:
        return sorted(
            n for n in os.listdir(root)
            if re.match(r"^cpu\d+$", n))
    except OSError:
        return []


def read_pm_qos(root: str = DEFAULT_CPU_ROOT) -> list[dict]:
    """Returns per-CPU dicts with pm_qos and cpuidle counts."""
    out: list[dict] = []
    for cpu in list_cpu_dirs(root):
        d = os.path.join(root, cpu)
        latency = _read_int(os.path.join(
            d, "power", "pm_qos_resume_latency_us"))
        cpuidle_dir = os.path.join(d, "cpuidle")
        try:
            states = [
                s for s in os.listdir(cpuidle_dir)
                if s.startswith("state")]
        except OSError:
            states = []
        out.append({
            "cpu": cpu,
            "pm_qos_resume_latency_us": latency,
            "cpuidle_state_count": len(states),
        })
    return out


def find_dma_latency_holders(proc_root: str = DEFAULT_PROC_ROOT,
                              dev_path: str = CPU_DMA_LATENCY_DEV
                              ) -> tuple[list[dict], int, int]:
    """Returns (holders, pids_scanned, pids_inaccessible).

    Each holder is {pid, comm}.  Scans /proc/<pid>/fd/*
    readlinks for any that resolve to dev_path."""
    holders: list[dict] = []
    pids_scanned = 0
    pids_inaccessible = 0
    try:
        entries = os.listdir(proc_root)
    except OSError:
        return ([], 0, 0)
    for name in entries:
        if not name.isdigit():
            continue
        fd_dir = os.path.join(proc_root, name, "fd")
        try:
            fds = os.listdir(fd_dir)
        except (OSError, PermissionError):
            pids_inaccessible += 1
            continue
        pids_scanned += 1
        for fd in fds:
            try:
                target = os.readlink(
                    os.path.join(fd_dir, fd))
            except OSError:
                continue
            if target == dev_path:
                comm = _read_text(
                    os.path.join(proc_root, name, "comm"))
                holders.append({"pid": int(name),
                                   "comm": comm or ""})
                break
    return (holders, pids_scanned, pids_inaccessible)


def classify(cpus: list[dict],
             holders: list[dict],
             pids_scanned: int,
             pids_inaccessible: int) -> dict:
    if not cpus:
        return {"verdict": "unknown",
                "reason": "/sys/devices/system/cpu has no "
                          "cpu<N> entries."}

    # Identify CPUs with surfaced pm_qos sysfs
    pm_qos_present = [
        c for c in cpus
        if c["pm_qos_resume_latency_us"] is not None]
    if not pm_qos_present:
        return {"verdict": "unknown",
                "reason": (
                    "No CPU exposes "
                    "/sys/.../power/pm_qos_resume_latency_us "
                    "— kernel built without PM-QoS sysfs.")}

    # CPUs with cpuidle directories
    cpuidle_count = sum(
        1 for c in cpus if c["cpuidle_state_count"] > 0)

    clamped = [
        c for c in pm_qos_present
        if c["pm_qos_resume_latency_us"] != 0]
    clamp_ratio = len(clamped) / len(pm_qos_present)

    # 1. err — majority clamped
    if clamp_ratio > 0.5:
        worst = max(
            clamped,
            key=lambda c: c["pm_qos_resume_latency_us"])
        return {
            "verdict": "pm_qos_latency_clamped_majority",
            "reason": (
                f"{len(clamped)} of {len(pm_qos_present)} "
                "CPUs have pm_qos_resume_latency_us != 0 "
                f"(worst: {worst['cpu']} = "
                f"{worst['pm_qos_resume_latency_us']} µs) "
                "— deep C-states are unreachable, idle "
                "wattage leaks."),
            "clamp_count": len(clamped),
            "cpu_count": len(pm_qos_present)}

    # 2. warn — external holder on /dev/cpu_dma_latency
    external_holders = [
        h for h in holders
        if h["comm"] not in _BENIGN_HOLDER_COMMS]
    if external_holders:
        first = external_holders[0]
        return {
            "verdict": "cpu_dma_latency_held_external",
            "reason": (
                f"PID {first['pid']} ({first['comm']}) "
                "has /dev/cpu_dma_latency open — likely "
                "pinning PM-QoS constraint and blocking "
                "deep idle."),
            "pid": first["pid"], "comm": first["comm"],
            "holder_count": len(external_holders)}

    # 3. accent — mixed clamping
    if clamped:
        return {"verdict": "pm_qos_mixed",
                "reason": (
                    f"{len(clamped)} of "
                    f"{len(pm_qos_present)} CPUs are "
                    "clamped, the rest are unconstrained "
                    "— accidental shielding leftover."),
                "clamp_count": len(clamped)}

    # 4. accent — no cpuidle states at all
    if cpuidle_count == 0:
        return {"verdict": "no_cpuidle",
                "reason": (
                    "No CPU surfaces cpuidle/state* — VM "
                    "or BIOS disabled idle states. PM-QoS "
                    "is moot in this configuration.")}

    # 5. requires_root — couldn't see any /proc/<pid>/fd
    if pids_scanned == 0 and pids_inaccessible > 0:
        return {"verdict": "requires_root",
                "reason": (
                    f"Could not enumerate /proc/<pid>/fd "
                    f"for any of {pids_inaccessible} PIDs. "
                    "Re-run as root for the holder scan.")}

    return {"verdict": "ok",
            "reason": (
                f"{len(pm_qos_present)} CPUs at "
                "NO_CONSTRAINT, "
                f"{cpuidle_count} CPUs expose cpuidle "
                f"states ; {len(holders)} benign holder(s) "
                f"of {CPU_DMA_LATENCY_DEV}.")}


def status(config: Optional[dict] = None,
           cpu_root: str = DEFAULT_CPU_ROOT,
           proc_root: str = DEFAULT_PROC_ROOT,
           dev_path: str = CPU_DMA_LATENCY_DEV) -> dict:
    cpus = read_pm_qos(cpu_root)
    holders, pids_scanned, pids_inaccessible = (
        find_dma_latency_holders(proc_root, dev_path))
    verdict = classify(cpus, holders, pids_scanned,
                        pids_inaccessible)
    return {
        "ok": verdict["verdict"] not in (
            "unknown",
            "pm_qos_latency_clamped_majority"),
        "cpu_count": len(cpus),
        "clamped_count": sum(
            1 for c in cpus
            if c["pm_qos_resume_latency_us"] not in (
                None, 0)),
        "holders": holders,
        "pids_scanned": pids_scanned,
        "pids_inaccessible": pids_inaccessible,
        "verdict": verdict,
    }
