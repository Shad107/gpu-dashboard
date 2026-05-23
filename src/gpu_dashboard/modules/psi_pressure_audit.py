"""Module psi_pressure_audit — /proc/pressure/* PSI stalls (R&D #53.1).

Pressure Stall Information (CONFIG_PSI=y) tracks the % of wall-time
the host had ≥1 task ('some') or every task ('full') stalled on
each of cpu / memory / io. Sustained values surface workloads that
*aren't* hitting OOM or thermal trips but still tank tokens/sec.

On an LLM rig the common foot-guns :

* Memory full stalls > 1 % over 300 s = the host is paging /
  zswap-ing while inference runs ; never OOMs, but every alloc
  blocks on writeback.
* IO some stalls > 5 % = swap is on a slow disk, or model
  load is hitting a saturated NVMe queue.
* CPU some stalls > 5 % = preempted by housekeeping
  (backup, builder, oomd) sharing the host.

Reads :
  /proc/pressure/cpu              # only 'some' line
  /proc/pressure/memory           # 'some' + 'full'
  /proc/pressure/io               # 'some' + 'full'
  /proc/sys/kernel/sched_schedstats   # 0 = some PSI counters disabled

Verdicts (priority-ordered) :
  psi_disabled                /proc/pressure/* absent (kernel built
                              without CONFIG_PSI, or psi=off).
  memory_full_stall_high      memory.full avg300 > 1.0 %.
  io_some_stall_high          io.some avg300 > 5.0 %.
  cpu_some_stall_elevated     cpu.some avg300 > 5.0 %.
  ok                          everything below thresholds.
  unknown                     unparseable PSI file.

stdlib only.
"""
from __future__ import annotations

import os
import re
from typing import Dict, Optional


NAME = "psi_pressure_audit"


_PROC_PRESSURE = "/proc/pressure"
_PROC_SCHED_STATS = "/proc/sys/kernel/sched_schedstats"


# Default verdict thresholds (percent of wall-time).
_MEM_FULL_AVG300 = 1.0
_IO_SOME_AVG300 = 5.0
_CPU_SOME_AVG300 = 5.0


_LINE_RE = re.compile(
    r"^(?P<kind>some|full)\s+"
    r"avg10=(?P<a10>[0-9.]+)\s+"
    r"avg60=(?P<a60>[0-9.]+)\s+"
    r"avg300=(?P<a300>[0-9.]+)\s+"
    r"total=(?P<total>\d+)\s*$")


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


def parse_pressure(text: Optional[str]) -> Dict[str, dict]:
    """Parse /proc/pressure/<resource> into {some: {...}, full: {...}}."""
    out: Dict[str, dict] = {}
    if not text:
        return out
    for line in text.splitlines():
        m = _LINE_RE.match(line.strip())
        if not m:
            continue
        out[m.group("kind")] = {
            "avg10": float(m.group("a10")),
            "avg60": float(m.group("a60")),
            "avg300": float(m.group("a300")),
            "total": int(m.group("total")),
        }
    return out


def read_pressure(proc_pressure: str = _PROC_PRESSURE) -> dict:
    """Returns {available, cpu, memory, io}."""
    out: dict = {"available": False}
    if not os.path.isdir(proc_pressure):
        return out
    found_any = False
    for res in ("cpu", "memory", "io"):
        p = os.path.join(proc_pressure, res)
        parsed = parse_pressure(_read(p))
        if parsed:
            found_any = True
        out[res] = parsed
    out["available"] = found_any
    return out


def classify(pressure: dict, sched_schedstats: Optional[int]) -> dict:
    if not pressure.get("available"):
        return {"verdict": "psi_disabled",
                "reason": ("/proc/pressure/* unavailable — kernel "
                          "built without CONFIG_PSI or booted "
                          "with psi=off."),
                "recommendation": _recipe_enable_psi()}

    mem = pressure.get("memory", {}).get("full") or {}
    iosome = pressure.get("io", {}).get("some") or {}
    cpusome = pressure.get("cpu", {}).get("some") or {}

    mem_a300 = mem.get("avg300", 0.0)
    io_a300 = iosome.get("avg300", 0.0)
    cpu_a300 = cpusome.get("avg300", 0.0)

    if mem_a300 > _MEM_FULL_AVG300:
        return {"verdict": "memory_full_stall_high",
                "reason": (f"memory.full avg300 = {mem_a300:.2f}% — "
                          f"every task is blocked on memory for "
                          f"{mem_a300:.2f}% of wall-time. Likely "
                          f"zswap / paging under inference."),
                "recommendation": _recipe_mem_pressure()}

    if io_a300 > _IO_SOME_AVG300:
        return {"verdict": "io_some_stall_high",
                "reason": (f"io.some avg300 = {io_a300:.2f}% — "
                          f"≥1 task stalled on IO over the last "
                          f"5 min. Check swap device + queue depth."),
                "recommendation": _recipe_io_pressure()}

    if cpu_a300 > _CPU_SOME_AVG300:
        return {"verdict": "cpu_some_stall_elevated",
                "reason": (f"cpu.some avg300 = {cpu_a300:.2f}% — "
                          f"runqueue contention. Backup / builder / "
                          f"oomd may be sharing the host."),
                "recommendation": _recipe_cpu_pressure()}

    return {"verdict": "ok",
            "reason": (f"PSI looks quiet (mem.full avg300="
                      f"{mem_a300:.2f}%, io.some={io_a300:.2f}%, "
                      f"cpu.some={cpu_a300:.2f}%)."),
            "recommendation": ""}


def status(config=None,
            proc_pressure: str = _PROC_PRESSURE,
            proc_sched_stats: str = _PROC_SCHED_STATS) -> dict:
    pressure = read_pressure(proc_pressure)
    sched_stats = _read_int(proc_sched_stats)
    ok = pressure.get("available", False)
    verdict = classify(pressure, sched_stats)
    return {"ok": ok,
              "pressure": pressure,
              "sched_schedstats": sched_stats,
              "verdict": verdict}


# ── recovery recipes ────────────────────────────────────────────

def _recipe_enable_psi() -> str:
    return ("# PSI requires CONFIG_PSI=y at kernel build time.\n"
            "# Distro kernels usually have it ; if it's compiled in\n"
            "# but disabled, add 'psi=1' to GRUB_CMDLINE_LINUX :\n"
            "sudo sed -i 's/GRUB_CMDLINE_LINUX=\\\"/&psi=1 /' /etc/default/grub\n"
            "sudo update-grub  # debian/ubuntu\n"
            "# Reboot, then verify : ls /proc/pressure/\n")


def _recipe_mem_pressure() -> str:
    return ("# Identify the culprit cgroups :\n"
            "find /sys/fs/cgroup -name memory.pressure -exec grep -H 'full' {} \\; | sort -t= -k4 -rn | head\n"
            "# Check zswap / swap activity :\n"
            "grep -E 'SwapCached|SwapFree|Dirty|Writeback' /proc/meminfo\n"
            "# Consider raising vm.swappiness=10 + disabling zswap if\n"
            "# the working set fits in RAM at quiet times.\n")


def _recipe_io_pressure() -> str:
    return ("# Find what's hammering IO :\n"
            "find /sys/fs/cgroup -name io.pressure -exec grep -H 'some' {} \\; | sort -t= -k4 -rn | head\n"
            "# Check swap device + queue depth :\n"
            "cat /proc/swaps\n"
            "for q in /sys/block/*/queue/nr_requests; do echo \"$q: $(cat $q)\"; done\n")


def _recipe_cpu_pressure() -> str:
    return ("# Find which units / containers are contending for CPU :\n"
            "find /sys/fs/cgroup -name cpu.pressure -exec grep -H 'some' {} \\; | sort -t= -k4 -rn | head\n"
            "# Then pin inference to dedicated cores via systemd\n"
            "# CPUAffinity= or `taskset -c`.\n")
