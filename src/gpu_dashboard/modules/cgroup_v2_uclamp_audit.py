"""Module cgroup_v2_uclamp_audit — cgroup v2 cpu.uclamp +
memory.zswap posture (R&D #103.4).

Two newer cgroup-v2 surfaces that none of the eight existing
cgroup modules read :

  cpu.uclamp.min            scheduler-assist floor (0..100 %)
  cpu.uclamp.max            ceiling ('max' or 0..100 %)
  memory.zswap.max          per-cgroup zswap budget
  memory.zswap.writeback    0 = pin pages in zswap, 1 = allow
                            writeback to swap device

Real-world failure mode: a game launcher / sandbox / systemd
unit accidentally sets cpu.uclamp.max=50 on user.slice and
GPU-feeder threads run at half speed.

Existing cgroup modules (cgroup_cpuio, cgroup_delegate_audit,
cgroup_io_stat_audit, cgroup_memevents_audit, cgroup_root_audit,
cgroup_v2_memory_peak_audit, cgroup_pids_controller_audit,
cgroup_memcap) — none of them grep uclamp or memory.zswap.

Reads :

  /sys/fs/cgroup/cgroup.controllers
  /sys/fs/cgroup/system.slice/cpu.uclamp.{min,max}
  /sys/fs/cgroup/user.slice/cpu.uclamp.{min,max}
  /sys/fs/cgroup/<slice>/memory.zswap.{max,writeback}

Verdicts (worst-first) :

  uclamp_max_below_100         err     a top-level slice has
                                       cpu.uclamp.max < 100 —
                                       broad CPU ceiling.
  zswap_disabled_on_slice      warn    memory.zswap.max=0 on a
                                       top-level slice.
  uclamp_min_boosted           accent  cpu.uclamp.min > 0 on a
                                       slice — unusual boost.
  zswap_writeback_off          accent  memory.zswap.writeback=0
                                       — pages pinned in zswap.
  ok                                   all defaults.
  requires_root                        cgroup tree unreadable.
  unknown                              cgroup v2 not mounted.

stdlib only.
"""
from __future__ import annotations

import os
from typing import Optional

NAME = "cgroup_v2_uclamp_audit"

DEFAULT_CGROUP_ROOT = "/sys/fs/cgroup"

_SLICES = ("system.slice", "user.slice")


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


def parse_uclamp(value: Optional[str]) -> Optional[float]:
    """cpu.uclamp.{min,max} is a percentage 0.0..100.0,
    or the literal 'max'."""
    if value is None:
        return None
    v = value.strip()
    if v == "max":
        return 100.0
    try:
        return float(v)
    except ValueError:
        return None


def walk_slices(root: str = DEFAULT_CGROUP_ROOT) -> list:
    """Return list of {path, uclamp_min, uclamp_max,
    zswap_max, zswap_writeback}."""
    out: list = []
    for s in _SLICES:
        d = os.path.join(root, s)
        if not os.path.isdir(d):
            continue
        out.append({
            "path": s,
            "uclamp_min": parse_uclamp(
                _read_str(os.path.join(d, "cpu.uclamp.min"))),
            "uclamp_max": parse_uclamp(
                _read_str(os.path.join(d, "cpu.uclamp.max"))),
            "zswap_max": _read_str(
                os.path.join(d, "memory.zswap.max")),
            "zswap_writeback": _read_int(
                os.path.join(d, "memory.zswap.writeback")),
        })
    return out


def classify(v2_present: bool,
             v2_readable: bool,
             slices: list) -> dict:
    if not v2_present:
        return {"verdict": "unknown",
                "reason": (
                    "cgroup v2 not mounted at "
                    "/sys/fs/cgroup.")}
    if not v2_readable:
        return {"verdict": "requires_root",
                "reason": (
                    "cgroup v2 tree unreadable — "
                    "re-run as root.")}
    if not slices:
        return {"verdict": "unknown",
                "reason": (
                    "Neither system.slice nor user.slice "
                    "found under /sys/fs/cgroup.")}

    # err — uclamp.max below 100 on any slice
    capped = [
        s for s in slices
        if (s["uclamp_max"] is not None
            and s["uclamp_max"] < 100.0)]
    if capped:
        names = [
            f"{s['path']}={s['uclamp_max']:.0f}%"
            for s in capped]
        return {
            "verdict": "uclamp_max_below_100",
            "reason": (
                f"{len(capped)} slice(s) have "
                f"cpu.uclamp.max < 100 %: {names}. "
                "Broad CPU ceiling — workloads inside "
                "cap at this fraction.")}

    # warn — zswap.max = 0
    zswap_off = [
        s for s in slices
        if (s["zswap_max"] is not None
            and s["zswap_max"].strip() == "0")]
    if zswap_off:
        names = [s["path"] for s in zswap_off]
        return {
            "verdict": "zswap_disabled_on_slice",
            "reason": (
                f"{len(zswap_off)} slice(s) have "
                f"memory.zswap.max=0: {names}. zswap "
                "explicitly disabled for these workloads.")}

    # accent — uclamp.min > 0
    boosted = [
        s for s in slices
        if (s["uclamp_min"] is not None
            and s["uclamp_min"] > 0.0)]
    if boosted:
        names = [
            f"{s['path']}={s['uclamp_min']:.0f}%"
            for s in boosted]
        return {
            "verdict": "uclamp_min_boosted",
            "reason": (
                f"{len(boosted)} slice(s) have "
                f"cpu.uclamp.min > 0: {names}. Unusual "
                "scheduler boost ; verify it's intentional.")}

    # accent — zswap.writeback off
    wb_off = [
        s for s in slices
        if s["zswap_writeback"] == 0]
    if wb_off:
        names = [s["path"] for s in wb_off]
        return {
            "verdict": "zswap_writeback_off",
            "reason": (
                f"{len(wb_off)} slice(s) have "
                f"memory.zswap.writeback=0: {names}. "
                "Compressed pages pinned in RAM, can't "
                "drift to swap.")}

    return {"verdict": "ok",
            "reason": (
                f"{len(slices)} top-level slice(s) ; "
                "uclamp / zswap all at defaults.")}


def status(config: Optional[dict] = None,
           root: str = DEFAULT_CGROUP_ROOT) -> dict:
    v2_present = os.path.isfile(
        os.path.join(root, "cgroup.controllers"))
    v2_readable = (
        v2_present and os.access(root, os.R_OK))
    slices = walk_slices(root) if v2_readable else []
    verdict = classify(v2_present, v2_readable, slices)
    return {
        "ok": verdict["verdict"] == "ok",
        "slice_count": len(slices),
        "slices": slices,
        "verdict": verdict,
    }
