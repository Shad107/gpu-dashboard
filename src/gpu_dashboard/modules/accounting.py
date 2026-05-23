"""Module accounting — NVML accounting harvester (R&D #24.1).

NVML keeps per-PID accounting in a circular buffer when
`accounting_mode` is enabled on the GPU. This persists peak VRAM,
average GPU/mem utilization, and wall-time for terminated PIDs —
exactly the post-mortem data missing when llama-server / ComfyUI /
vLLM crash mid-job.

NVIDIA gives us :
  nvidia-smi --query-gpu=accounting.mode
    → 'Enabled' or 'Disabled'
  nvidia-smi --query-accounted-apps=
      gpu_uuid,pid,gpu_util,mem_util,max_memory_usage,time
    → one CSV row per accounted PID (alive or dead)

Enabling accounting needs root :
  sudo nvidia-smi --accounting-mode=1

This module reads what's there, surfaces an enable-command if it's
off, and persists a rolling log so records survive `nvidia-smi -caa`
buffer flushes.

stdlib only.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import threading
import time
from typing import Optional


NAME = "accounting"


_LOG_PATH = "~/.config/gpu-dashboard/accounting_log.json"
_MAX_RECORDS = 500

_lock = threading.Lock()


def log_path() -> str:
    return os.path.expanduser(_LOG_PATH)


def load_log() -> list:
    p = log_path()
    if not os.path.exists(p):
        return []
    try:
        with open(p) as f:
            d = json.load(f)
        return d if isinstance(d, list) else []
    except (OSError, json.JSONDecodeError):
        return []


def save_log(records: list) -> None:
    p = log_path()
    os.makedirs(os.path.dirname(p), exist_ok=True)
    records = records[-_MAX_RECORDS:]
    with open(p, "w") as f:
        json.dump(records, f)


def query_accounting_mode(timeout: float = 2.0) -> Optional[str]:
    """Return 'Enabled' / 'Disabled' / None."""
    if not shutil.which("nvidia-smi"):
        return None
    try:
        r = subprocess.run(
            ["nvidia-smi", "--query-gpu=accounting.mode",
             "--format=csv,noheader,nounits"],
            capture_output=True, text=True, timeout=timeout,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired, OSError):
        return None
    if r.returncode != 0:
        return None
    line = r.stdout.strip().splitlines()
    return line[0].strip() if line else None


def query_accounted_apps(timeout: float = 3.0) -> Optional[list[dict]]:
    """Return list of accounted PID records. None on driver error."""
    if not shutil.which("nvidia-smi"):
        return None
    try:
        r = subprocess.run(
            ["nvidia-smi",
             "--query-accounted-apps="
             "gpu_uuid,pid,gpu_util,mem_util,max_memory_usage,time",
             "--format=csv,noheader,nounits"],
            capture_output=True, text=True, timeout=timeout,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired, OSError):
        return None
    if r.returncode != 0:
        return None
    return parse_accounted_csv(r.stdout)


def parse_accounted_csv(text: str) -> list[dict]:
    """Parse the CSV output of --query-accounted-apps."""
    out: list[dict] = []
    for line in text.splitlines():
        parts = [p.strip() for p in line.split(",")]
        if len(parts) < 6 or not parts[1].isdigit():
            continue
        try:
            pid = int(parts[1])
            gpu_util = _safe_int(parts[2])
            mem_util = _safe_int(parts[3])
            max_mem = _safe_int(parts[4])
            wall_ms = _safe_int(parts[5])
        except ValueError:
            continue
        out.append({
            "gpu_uuid": parts[0],
            "pid": pid,
            "gpu_util_pct": gpu_util,
            "mem_util_pct": mem_util,
            "max_memory_mib": max_mem,
            "wall_time_ms": wall_ms,
        })
    return out


def _safe_int(s: str) -> Optional[int]:
    s = s.strip()
    if not s or s.lower() in ("n/a", "[n/a]", "not supported"):
        return None
    try:
        return int(s)
    except ValueError:
        return None


def merge_into_log(existing: list, new_records: list,
                    now_ts: Optional[float] = None) -> list:
    """Deduplicate by (gpu_uuid, pid) + add 'observed_at' timestamp.
    Updates pre-existing records with newer stats."""
    if now_ts is None:
        now_ts = time.time()
    by_key: dict = {}
    for r in existing:
        key = (r.get("gpu_uuid"), r.get("pid"))
        by_key[key] = r
    for r in new_records:
        key = (r.get("gpu_uuid"), r.get("pid"))
        existing_r = by_key.get(key, {})
        merged = {**existing_r, **r}
        merged["observed_at"] = int(now_ts)
        if "first_seen_at" not in merged:
            merged["first_seen_at"] = int(now_ts)
        by_key[key] = merged
    return sorted(by_key.values(), key=lambda r: r.get("observed_at", 0))


def aggregate_by_command(records: list,
                          proc_root: str = "/proc") -> list[dict]:
    """Group accounted records by /proc/<pid>/comm. Returns
    [{comm, count, total_wall_ms, max_memory_mib, mean_gpu_util}, ...]"""
    by_comm: dict = {}
    for r in records:
        comm = _read_comm(r["pid"], proc_root) or "?"
        entry = by_comm.setdefault(comm, {"comm": comm, "count": 0,
                                            "total_wall_ms": 0,
                                            "max_memory_mib": 0,
                                            "gpu_util_sum": 0,
                                            "gpu_util_n": 0})
        entry["count"] += 1
        if r.get("wall_time_ms"):
            entry["total_wall_ms"] += r["wall_time_ms"]
        if r.get("max_memory_mib") is not None:
            entry["max_memory_mib"] = max(entry["max_memory_mib"],
                                            r["max_memory_mib"])
        if r.get("gpu_util_pct") is not None:
            entry["gpu_util_sum"] += r["gpu_util_pct"]
            entry["gpu_util_n"] += 1
    out: list[dict] = []
    for entry in by_comm.values():
        n = entry.pop("gpu_util_n")
        s = entry.pop("gpu_util_sum")
        entry["mean_gpu_util_pct"] = round(s / n, 1) if n else None
        out.append(entry)
    out.sort(key=lambda e: -e["count"])
    return out


def _read_comm(pid: int, proc_root: str) -> str:
    """Read /proc/<pid>/comm if the PID is still alive."""
    try:
        with open(os.path.join(proc_root, str(pid), "comm")) as f:
            return f.read().strip()
    except OSError:
        return ""


def status(cfg=None) -> dict:
    """Aggregate snapshot."""
    mode = query_accounting_mode()
    if mode is None:
        return {
            "ok": False,
            "reason": "nvidia-smi unreachable.",
            "accounting_mode": None,
        }
    if mode != "Enabled":
        return {
            "ok": True,
            "accounting_mode": mode,
            "enable_command": "sudo nvidia-smi --accounting-mode=1",
            "advisory": ("Accounting is OFF. Enable it to start recording "
                          "post-mortem stats for crashed CUDA processes."),
            "records": [],
            "by_command": [],
            "record_count": 0,
        }
    new_records = query_accounted_apps() or []
    with _lock:
        log = load_log()
        merged = merge_into_log(log, new_records)
        save_log(merged)
    by_cmd = aggregate_by_command(merged)
    return {
        "ok": True,
        "accounting_mode": mode,
        "records": merged[-30:],
        "by_command": by_cmd[:20],
        "record_count": len(merged),
    }
