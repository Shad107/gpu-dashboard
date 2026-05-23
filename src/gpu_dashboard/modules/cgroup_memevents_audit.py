"""Module cgroup_memevents_audit — cgroup v2 memory events (R&D #50.2).

Walks /sys/fs/cgroup/ for {memory.events, memory.swap.events,
memory.peak} per cgroup. Surfaces OOM events, swap failures, and
peak usage tied to specific systemd units / pods / scope groups.

memory.events file format (one key + count per line) :
  low <N>         times memory.low threshold breached
  high <N>        times memory.high throttled
  max <N>         times memory.max hit (allocation blocked)
  oom <N>         times the cgroup OOM was attempted
  oom_kill <N>    processes killed by cgroup OOM
  oom_group_kill <N>  group-level kills

memory.swap.events :
  high <N>        memory.swap.high throttle
  max <N>         memory.swap.max hit
  fail <N>        swap-out failed (out of swap space)

Verdicts (priority-ordered) :
  oom_in_unit         ≥1 cgroup has oom_kill > 0 → process was
                      killed by cgroup OOM. Critical — likely
                      llama-server / inference daemon killed.
  swap_failures       ≥1 cgroup has swap fail > 0 → swap-out
                      tried but couldn't allocate. Bigger swap
                      device needed or zswap (#41.1 zswap_zram).
  high_pressure       ≥ 5 cgroups have memory.events high or max
                      > 0 → many cgroups throttled.
  ok                  no OOM / swap failures / pressure events.
  no_cgroup_v2        /sys/fs/cgroup/cgroup.controllers absent
                      (cgroup v1 only — exotic on modern systems).
  unknown             /sys/fs/cgroup unreadable.

stdlib only.
"""
from __future__ import annotations

import os
from typing import Optional


NAME = "cgroup_memevents_audit"


_SYS_CGROUP = "/sys/fs/cgroup"


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


def is_cgroup_v2(sys_cgroup: str = _SYS_CGROUP) -> bool:
    return os.path.isfile(os.path.join(sys_cgroup,
                                            "cgroup.controllers"))


def parse_kv(text: Optional[str]) -> dict:
    """memory.events / memory.swap.events : 'key value' per line."""
    out: dict = {}
    if not text:
        return out
    for line in text.splitlines():
        parts = line.split()
        if len(parts) != 2:
            continue
        try:
            out[parts[0]] = int(parts[1])
        except ValueError:
            continue
    return out


def walk_units(sys_cgroup: str = _SYS_CGROUP,
                 max_units: int = 200) -> list:
    """Walk /sys/fs/cgroup recursively for directories that contain
    a memory.events file. Cap at max_units to avoid /proc.* like
    /sys/fs/cgroup/user.slice/user-1000.slice/user@1000.service
    /app.slice/snap.firefox.firefox-... blowup.
    Skip leaf cgroups (kthreads, etc) that don't have memory.events.
    """
    out: list = []
    if not os.path.isdir(sys_cgroup):
        return out
    for root, dirs, files in os.walk(sys_cgroup):
        if "memory.events" not in files:
            continue
        events = parse_kv(_read(os.path.join(root, "memory.events")))
        swap_events = parse_kv(_read(os.path.join(
            root, "memory.swap.events")))
        peak = _read_int(os.path.join(root, "memory.peak"))
        rel = os.path.relpath(root, sys_cgroup) or "/"
        out.append({
            "path": rel,
            "events": events,
            "swap_events": swap_events,
            "peak_bytes": peak,
        })
        if len(out) >= max_units:
            break
    return out


_RECIPE_OOM = (
    "# Process(es) killed by cgroup OOM. Inspect via :\n"
    "journalctl -u <unit> --since '1 hour ago' | grep -i oom\n"
    "# Then either :\n"
    "#  1. Bump memory.max for that unit :\n"
    "sudo systemctl edit <unit>\n"
    "#  [Service]\n"
    "#  MemoryMax=16G\n"
    "#  2. Or reduce the process's memory footprint (smaller\n"
    "#     model quant, smaller context window)."
)

_RECIPE_SWAP_FAIL = (
    "# Cgroup swap.fail > 0 — kernel tried to swap out but no\n"
    "# space available. Options :\n"
    "#  1. Enable zswap to compress in RAM first (see #41.1) :\n"
    "echo 1 | sudo tee /sys/module/zswap/parameters/enabled\n"
    "#  2. Add swap (zram or NVMe-backed) :\n"
    "sudo fallocate -l 8G /swap2.img && sudo mkswap /swap2.img \\\n"
    "    && sudo swapon /swap2.img\n"
    "#  3. Or relax memory.max for the offending cgroup."
)

_RECIPE_PRESSURE = (
    "# Multiple cgroups hitting memory.high / memory.max throttles.\n"
    "# Identify the worst offenders from the UI list, then :\n"
    "#  - Bump memory.max where it's a legitimate need.\n"
    "#  - Reduce concurrent inference jobs.\n"
    "#  - See PSI module (#32.1) for system-wide pressure."
)


_PRESSURE_THRESHOLD = 5


def classify(units: list) -> dict:
    if not units:
        return {"verdict": "no_cgroup_v2",
                "reason": ("No cgroup-v2 memory.events files found. "
                           "Either CONFIG_CGROUPS=n or cgroup v1 "
                           "only."),
                "recommendation": ""}
    oom = [u for u in units
            if (u["events"].get("oom_kill", 0) > 0)]
    if oom:
        names = ", ".join(
            f"{u['path']} (oom_kill={u['events']['oom_kill']})"
            for u in oom[:3])
        return {"verdict": "oom_in_unit",
                "reason": (f"{len(oom)} cgroup(s) with oom_kill > 0 — "
                           f"process(es) killed by cgroup OOM. "
                           f"{names}"),
                "recommendation": _RECIPE_OOM}
    swap_fail = [u for u in units
                  if (u["swap_events"].get("fail", 0) > 0)]
    if swap_fail:
        names = ", ".join(
            f"{u['path']} (fail={u['swap_events']['fail']})"
            for u in swap_fail[:3])
        return {"verdict": "swap_failures",
                "reason": (f"{len(swap_fail)} cgroup(s) with swap "
                           f"fail > 0. {names}"),
                "recommendation": _RECIPE_SWAP_FAIL}
    pressured = [u for u in units
                  if (u["events"].get("high", 0) > 0
                      or u["events"].get("max", 0) > 0)]
    if len(pressured) >= _PRESSURE_THRESHOLD:
        return {"verdict": "high_pressure",
                "reason": (f"{len(pressured)} cgroup(s) with "
                           f"memory.high or memory.max events > 0 — "
                           f"many units throttled."),
                "recommendation": _RECIPE_PRESSURE}
    return {"verdict": "ok",
            "reason": (f"{len(units)} cgroup(s) audited ; no OOM "
                       f"kills, no swap failures, "
                       f"{len(pressured)} with memory.high or "
                       f"memory.max events (below "
                       f"{_PRESSURE_THRESHOLD} threshold)."),
            "recommendation": ""}


def status(cfg=None) -> dict:
    if not os.path.isdir(_SYS_CGROUP) \
            or not is_cgroup_v2(_SYS_CGROUP):
        return {
            "ok": False,
            "verdict": {"verdict": "no_cgroup_v2",
                         "reason": ("cgroup-v2 not detected at "
                                    "/sys/fs/cgroup."),
                         "recommendation": ""},
            "units": [],
        }
    units = walk_units(_SYS_CGROUP)
    verdict = classify(units)
    # Sort + return top-30 by peak_bytes (or pressure count).
    units.sort(key=lambda u: -(u.get("peak_bytes") or 0))
    return {
        "ok": True,
        "unit_count": len(units),
        "top_units": units[:30],
        "verdict": verdict,
    }
