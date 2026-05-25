"""Module vmstat_reclaim_pressure_audit — direct reclaim +
compaction failure delta tracking (R&D #91.4).

No existing module computes vmstat *deltas* :

  * zoneinfo_audit         — per-zone snapshot of /proc/zoneinfo
  * psi_pressure_audit     — /proc/pressure/* (smoothed)
  * buddyinfo_frag         — /proc/buddyinfo static
  * thp_audit              — THP on/off policy
  * vm_sysctl_audit        — VM tunables only

This audit owns delta tracking of the vmstat reclaim /
compaction / OOM counters by persisting prior values under
$XDG_CONFIG_HOME/gpu-dashboard/vmstat_prev.json (default
~/.config/gpu-dashboard/). Direct-reclaim + compaction
failure trends are the gold standard for catching the
"12-hour LLM session that died with one OOM at 4am" pattern
that PSI smooths over.

Reads :

  /proc/vmstat                            cumulative counters
  /proc/sys/vm/min_free_kbytes            zone watermark base
  /proc/sys/vm/watermark_scale_factor     scale (default 10)
  /proc/meminfo (MemTotal)                for big-box check
  vmstat_prev.json                        persisted prior

Verdicts (worst-first) :

  oom_or_direct_reclaim_heavy  err   oom_kill delta > 0 OR
                                     pgsteal_direct / (direct
                                     + kswapd) > 0.5 on
                                     non-trivial activity.
  compaction_failing           warn  compact_fail delta >
                                     compact_success delta in
                                     window (THP allocations
                                     collapsing).
  watermarks_loose_big_box     accent watermark_scale_factor
                                     still 10 on > 32 GiB
                                     box (default too low for
                                     fast kswapd kick-in).
  ok                          all deltas healthy.
  unknown                     no prior snapshot (first run).
  prev_snapshot_corrupt       prev json unreadable / wrong
                              shape (treat as unknown but
                              persist current state).

The module ALWAYS writes the current counters back to the
state file so the next invocation has a baseline.

stdlib only.
"""
from __future__ import annotations

import json
import os
import re
from typing import Optional

NAME = "vmstat_reclaim_pressure_audit"

DEFAULT_VMSTAT = "/proc/vmstat"
DEFAULT_PROC_SYS_VM = "/proc/sys/vm"
DEFAULT_PROC_MEMINFO = "/proc/meminfo"


def _default_state_path() -> str:
    base = (os.environ.get("XDG_CONFIG_HOME")
            or os.path.expanduser("~/.config"))
    return os.path.join(
        base, "gpu-dashboard", "vmstat_prev.json")


# Counters we care about.
_COUNTERS = (
    "pgsteal_kswapd", "pgsteal_direct",
    "pgscan_kswapd", "pgscan_direct",
    "oom_kill",
    "compact_stall", "compact_fail", "compact_success",
    "thp_fault_fallback",
)

# Threshold for direct-reclaim ratio (delta-based).
_DIRECT_RECLAIM_RATIO_THRESHOLD = 0.5
# Minimum delta-activity to avoid ratio noise on near-idle.
_MIN_RECLAIM_ACTIVITY = 1000
# Big-box threshold for watermarks_loose_big_box accent.
_BIG_BOX_BYTES = 32 * 2**30
_DEFAULT_WATERMARK_SCALE = 10

_MEMTOTAL_RE = re.compile(r"^MemTotal:\s*(\d+)\s*kB",
                          re.MULTILINE)


def _read_text(path: str) -> Optional[str]:
    try:
        with open(path, "r", encoding="utf-8") as fh:
            return fh.read()
    except (OSError, PermissionError):
        return None


def _read_int(path: str) -> Optional[int]:
    t = _read_text(path)
    if not t:
        return None
    try:
        return int(t.strip())
    except ValueError:
        return None


def parse_vmstat(text: str) -> dict:
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


def parse_meminfo_total_bytes(text: str) -> Optional[int]:
    if not text:
        return None
    m = _MEMTOTAL_RE.search(text)
    if not m:
        return None
    try:
        return int(m.group(1)) * 1024
    except ValueError:
        return None


def load_prev_state(path: str) -> Optional[dict]:
    if not os.path.isfile(path):
        return None
    try:
        with open(path, "r", encoding="utf-8") as fh:
            data = json.load(fh)
        if isinstance(data, dict) and "counters" in data:
            return data
    except (OSError, json.JSONDecodeError):
        return None
    return None


def save_state(path: str, counters: dict) -> None:
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as fh:
            json.dump({"counters": counters}, fh)
    except OSError:
        pass


def compute_deltas(current: dict, prev: dict) -> dict:
    out: dict = {}
    for k in _COUNTERS:
        out[k] = (current.get(k, 0)
                  - prev.get("counters", {}).get(k, 0))
    return out


def classify(deltas: dict,
             has_prev: bool,
             watermark_scale_factor: Optional[int],
             mem_total: Optional[int]) -> dict:
    if not has_prev:
        return {"verdict": "unknown",
                "reason": (
                    "No prior /proc/vmstat snapshot — first "
                    "invocation, baseline saved. Re-check in "
                    "a few minutes for deltas.")}

    # err — OOM since last sample
    if deltas.get("oom_kill", 0) > 0:
        return {
            "verdict": "oom_or_direct_reclaim_heavy",
            "reason": (
                f"oom_kill delta = {deltas['oom_kill']} since "
                "last sample — OOM killer fired. Check `dmesg "
                "-T | grep -i oom` for victims."),
            "oom_kill": deltas["oom_kill"]}

    # err — direct reclaim ratio high under activity
    direct = max(0, deltas.get("pgsteal_direct", 0))
    kswapd = max(0, deltas.get("pgsteal_kswapd", 0))
    total = direct + kswapd
    if total >= _MIN_RECLAIM_ACTIVITY:
        ratio = direct / total if total > 0 else 0.0
        if ratio > _DIRECT_RECLAIM_RATIO_THRESHOLD:
            return {
                "verdict": "oom_or_direct_reclaim_heavy",
                "reason": (
                    f"Direct-reclaim ratio "
                    f"{100 * ratio:.0f}% (direct={direct}, "
                    f"kswapd={kswapd}) — workload is "
                    "stalling in alloc paths because kswapd "
                    "can't keep up."),
                "direct_ratio": ratio}

    # warn — compaction failing
    c_fail = max(0, deltas.get("compact_fail", 0))
    c_ok = max(0, deltas.get("compact_success", 0))
    if c_fail > c_ok and c_fail > 5:
        return {
            "verdict": "compaction_failing",
            "reason": (
                f"compact_fail = {c_fail} vs compact_success "
                f"= {c_ok} since last sample — memory "
                "fragmentation collapsing THP allocations."),
            "compact_fail": c_fail}

    # accent — watermark_scale_factor default on big box
    if (watermark_scale_factor == _DEFAULT_WATERMARK_SCALE
            and mem_total is not None
            and mem_total > _BIG_BOX_BYTES):
        return {
            "verdict": "watermarks_loose_big_box",
            "reason": (
                "vm.watermark_scale_factor = 10 (default) on "
                f"a {mem_total / 2**30:.0f} GiB box — kswapd "
                "kicks in too late, increasing direct-reclaim "
                "risk. Bump to 100-200."),
            "mem_total": mem_total}

    return {"verdict": "ok",
            "reason": (
                f"vmstat deltas healthy ; oom=0, "
                f"direct/kswapd={direct}/{kswapd}, "
                f"compact ok/fail={c_ok}/{c_fail}.")}


def status(config: Optional[dict] = None,
           vmstat_path: str = DEFAULT_VMSTAT,
           proc_sys_vm: str = DEFAULT_PROC_SYS_VM,
           meminfo_path: str = DEFAULT_PROC_MEMINFO,
           state_path: Optional[str] = None) -> dict:
    if state_path is None:
        state_path = _default_state_path()
    current = parse_vmstat(_read_text(vmstat_path) or "")
    prev = load_prev_state(state_path)
    has_prev = prev is not None
    deltas = compute_deltas(current, prev or {})
    wsf = _read_int(
        os.path.join(proc_sys_vm, "watermark_scale_factor"))
    mem_total = parse_meminfo_total_bytes(
        _read_text(meminfo_path) or "")
    verdict = classify(deltas, has_prev, wsf, mem_total)
    # Persist current counters for next invocation, only the
    # ones we track.
    to_save = {k: current.get(k, 0) for k in _COUNTERS}
    save_state(state_path, to_save)
    return {
        "ok": verdict["verdict"] == "ok",
        "has_prev_snapshot": has_prev,
        "watermark_scale_factor": wsf,
        "verdict": verdict,
    }
