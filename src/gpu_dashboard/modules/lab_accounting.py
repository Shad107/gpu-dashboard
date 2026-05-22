"""Module lab_accounting — per-user GPU usage tracking (R&D #14.2).

For shared lab/HPC boxes : attribute GPU-seconds, VRAM-GiB-hours, and
energy-Wh to the Linux user running each compute process.

Resolution path :
  1. nvidia-smi --query-compute-apps returns {pid, name, used_memory}
  2. For each PID, read /proc/<pid>/loginuid (audit subsystem) ;
     fall back to /proc/<pid>/status 'Uid:' line if loginuid is 'no
     auditing' (UINT_MAX = 4294967295).
  3. UID → username via pwd.getpwuid (best-effort).
  4. Optional alias file ~/.config/gpu-dashboard/users.allow :
       1000=alice
       1001=bob
     overrides usernames + acts as an allow-list when present.

Each evaluate() call is a SAMPLE — caller accumulates over time and
divides by sample rate to get gpu_seconds / vram_gb_hours / Wh.

stdlib only : os + pwd + subprocess.
"""
from __future__ import annotations

import os
import pwd
import subprocess
import time
from typing import Optional


NAME = "lab_accounting"

_ALIAS_PATH = "~/.config/gpu-dashboard/users.allow"
_UINT_MAX = 4_294_967_295  # 'no auditing' marker in /proc/<pid>/loginuid


def alias_path() -> str:
    return os.path.expanduser(_ALIAS_PATH)


def load_alias_map() -> dict:
    """Return {uid: alias} from the allow file. Empty if absent."""
    p = alias_path()
    out: dict = {}
    if not os.path.exists(p):
        return out
    try:
        with open(p) as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith("#"):
                    continue
                if "=" not in line:
                    continue
                k, v = line.split("=", 1)
                try:
                    out[int(k.strip())] = v.strip()
                except ValueError:
                    continue
    except OSError:
        pass
    return out


def read_loginuid(pid: int) -> Optional[int]:
    """Read /proc/<pid>/loginuid. Returns None if missing or no-audit marker."""
    try:
        with open(f"/proc/{pid}/loginuid") as f:
            v = int(f.read().strip())
    except (OSError, ValueError):
        return None
    if v == _UINT_MAX or v < 0:
        return None
    return v


def read_proc_uid(pid: int) -> Optional[int]:
    """Fallback : parse 'Uid:' line of /proc/<pid>/status."""
    try:
        with open(f"/proc/{pid}/status") as f:
            for line in f:
                if line.startswith("Uid:"):
                    parts = line.split()
                    if len(parts) >= 2:
                        return int(parts[1])
    except (OSError, ValueError):
        pass
    return None


def resolve_uid(pid: int) -> Optional[int]:
    """Best-effort PID → UID. Prefer loginuid (real interactive user)."""
    uid = read_loginuid(pid)
    if uid is not None:
        return uid
    return read_proc_uid(pid)


def uid_to_name(uid: int, alias_map: Optional[dict] = None) -> str:
    """UID → username, with optional alias override."""
    if alias_map and uid in alias_map:
        return alias_map[uid]
    try:
        return pwd.getpwuid(uid).pw_name
    except (KeyError, OSError):
        return f"uid_{uid}"


def probe_compute_apps() -> list:
    """nvidia-smi --query-compute-apps → list of {pid, name, used_memory_mib}."""
    try:
        r = subprocess.run(
            ["nvidia-smi",
             "--query-compute-apps=pid,name,used_memory",
             "--format=csv,noheader,nounits"],
            capture_output=True, text=True, timeout=3,
        )
    except (FileNotFoundError, subprocess.SubprocessError, OSError):
        return []
    if r.returncode != 0 or not r.stdout:
        return []
    out: list = []
    for line in r.stdout.strip().splitlines():
        parts = [p.strip() for p in line.split(",")]
        if len(parts) < 3:
            continue
        try:
            pid = int(parts[0])
            mib = int(parts[2])
        except ValueError:
            continue
        out.append({"pid": pid, "name": parts[1], "used_memory_mib": mib})
    return out


def evaluate(processes: Optional[list] = None, watts_total: Optional[float] = None,
             allow_only_uids: Optional[list] = None) -> dict:
    """Take ONE sample : attribute current VRAM + a share of total watts to
    each user with active compute processes.

    Returns :
      {ts, users: [{uid, name, pid_count, vram_used_mib, watts_share, processes: [...]}]}

    watts_total : total GPU power (from /api/state). Each user's share is
    proportional to their VRAM usage of the total VRAM in use.
    """
    if processes is None:
        processes = probe_compute_apps()
    alias_map = load_alias_map()

    # Group by UID
    by_uid: dict = {}
    total_vram_used = sum(int(p.get("used_memory_mib", 0)) for p in processes)
    for proc in processes:
        pid = int(proc.get("pid", 0))
        uid = resolve_uid(pid)
        if uid is None:
            uid = -1
        if allow_only_uids and uid not in allow_only_uids:
            continue
        rec = by_uid.setdefault(uid, {
            "uid": uid,
            "name": uid_to_name(uid, alias_map) if uid >= 0 else "unknown",
            "pid_count": 0,
            "vram_used_mib": 0,
            "processes": [],
        })
        rec["pid_count"] += 1
        rec["vram_used_mib"] += int(proc.get("used_memory_mib", 0))
        # Truncate exe name (full path is noisy)
        short_name = (proc.get("name") or "?").split("/")[-1]
        rec["processes"].append({"pid": pid, "name": short_name,
                                  "used_mib": int(proc.get("used_memory_mib", 0))})

    # Watts share : proportional to VRAM usage of the total VRAM in use
    if watts_total is not None and total_vram_used > 0:
        for rec in by_uid.values():
            share = rec["vram_used_mib"] / total_vram_used
            rec["watts_share"] = round(float(watts_total) * share, 1)
    else:
        for rec in by_uid.values():
            rec["watts_share"] = None

    return {
        "ts": int(time.time()),
        "users": sorted(by_uid.values(), key=lambda r: -r["vram_used_mib"]),
        "total_vram_used_mib": total_vram_used,
        "watts_total": watts_total,
    }


def aggregate_seconds(samples: list) -> dict:
    """Sum samples into per-user totals (gpu_seconds, vram_gb_hours, wh).
    Each sample is the dict from evaluate(). Caller supplies the list."""
    by_name: dict = {}
    if not samples:
        return {"users": []}
    # Compute average dt between samples for the integration step
    if len(samples) >= 2:
        first_ts = float(samples[0].get("ts", 0))
        last_ts = float(samples[-1].get("ts", 0))
        n_intervals = max(1, len(samples) - 1)
        avg_dt = max(1.0, (last_ts - first_ts) / n_intervals) if last_ts > first_ts else 5.0
    else:
        avg_dt = 5.0
    for snap in samples:
        for user in snap.get("users", []):
            key = user.get("name", "unknown")
            rec = by_name.setdefault(key, {
                "name": key,
                "uid": user.get("uid"),
                "gpu_seconds": 0,
                "vram_gb_hours": 0.0,
                "wh": 0.0,
                "sample_count": 0,
            })
            rec["gpu_seconds"] += int(avg_dt)
            rec["vram_gb_hours"] += user.get("vram_used_mib", 0) / 1024 * (avg_dt / 3600)
            if user.get("watts_share") is not None:
                rec["wh"] += float(user["watts_share"]) * (avg_dt / 3600)
            rec["sample_count"] += 1
    for rec in by_name.values():
        rec["vram_gb_hours"] = round(rec["vram_gb_hours"], 3)
        rec["wh"] = round(rec["wh"], 2)
    return {"users": sorted(by_name.values(), key=lambda r: -r["wh"])}
