"""Module nvme_swap — NVMe-as-VRAM-swap monitor (R&D #18.1).

When llama.cpp / Ollama / vLLM hit VRAM pressure, they spill to RAM
or — worse — to disk via mmap of weight files. On a small system this
silently shreds NVMe endurance because every inference re-reads (and
sometimes re-writes) GiB of weights. Users blame "slow GPU" when
really they're being bottlenecked by NVMe and burning TBW budget.

This module :

  1. Identifies LLM-related processes by comm pattern
  2. Reads each process's /proc/<pid>/status for VmSwap (committed swap)
  3. Reads /sys/block/<dev>/stat to compute writes-since-last-poll
  4. Pulls 'data_units_written' from smartctl (TBW used)
  5. Projects remaining endurance at the current write rate

stdlib only : subprocess + os.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import time
from typing import Optional


NAME = "nvme_swap"


# Match common LLM runtime executables. Patterns checked against /proc/<pid>/comm
# (which is truncated to 15 chars).
LLM_PROCESS_PATTERNS = (
    "ollama", "llama-server", "llama_server", "llama.cpp", "llamacpp",
    "vllm", "sglang", "exllamav2", "exllama", "comfyui", "comfy",
    "python", "python3",  # broad — refined by checking cmdline below
)

LLM_CMDLINE_HINTS = (
    "llama_cpp", "vllm.entrypoints", "ollama", "exllama", "ComfyUI",
    "diffusers", "transformers", "text-generation-webui",
)


def _read_comm(pid: int, proc_root: str = "/proc") -> str:
    try:
        with open(os.path.join(proc_root, str(pid), "comm")) as f:
            return f.read().strip()
    except OSError:
        return ""


def _read_cmdline(pid: int, proc_root: str = "/proc") -> str:
    try:
        with open(os.path.join(proc_root, str(pid), "cmdline"), "rb") as f:
            return f.read().replace(b"\x00", b" ").decode("utf-8", errors="replace")
    except OSError:
        return ""


def _read_status(pid: int, proc_root: str = "/proc") -> dict:
    """Parse /proc/<pid>/status into a dict."""
    out: dict = {}
    try:
        with open(os.path.join(proc_root, str(pid), "status")) as f:
            for line in f:
                if ":" not in line:
                    continue
                k, v = line.split(":", 1)
                out[k.strip()] = v.strip()
    except OSError:
        return {}
    return out


def _parse_kib(value: str) -> int:
    """Parse '12345 kB' → 12345 * 1024 bytes."""
    parts = value.split()
    if not parts or not parts[0].lstrip("-").isdigit():
        return 0
    n = int(parts[0])
    unit = parts[1].lower() if len(parts) > 1 else ""
    if unit.startswith("kb"):
        return n * 1024
    if unit.startswith("mb"):
        return n * 1024 * 1024
    return n


def is_llm_process(comm: str, cmdline: str) -> bool:
    """Heuristic : does this process look like an LLM runtime?"""
    if not comm:
        return False
    low = comm.lower()
    for pat in LLM_PROCESS_PATTERNS:
        if pat in low and pat != "python" and pat != "python3":
            return True
    if low.startswith("python"):
        for hint in LLM_CMDLINE_HINTS:
            if hint in cmdline:
                return True
    return False


def scan_llm_processes(proc_root: str = "/proc") -> list:
    """Return [{pid, comm, cmdline_short, swap_bytes, rss_bytes}, ...] for
    processes that look like LLM runtimes."""
    out: list = []
    try:
        names = os.listdir(proc_root)
    except OSError:
        return out
    for name in names:
        if not name.isdigit():
            continue
        pid = int(name)
        comm = _read_comm(pid, proc_root)
        cmdline = _read_cmdline(pid, proc_root)
        if not is_llm_process(comm, cmdline):
            continue
        st = _read_status(pid, proc_root)
        swap_bytes = _parse_kib(st.get("VmSwap", "0 kB"))
        rss_bytes = _parse_kib(st.get("VmRSS", "0 kB"))
        out.append({
            "pid": pid,
            "comm": comm,
            "cmdline_short": cmdline[:120],
            "swap_bytes": swap_bytes,
            "rss_bytes": rss_bytes,
        })
    return out


def list_nvme_devices(sys_root: str = "/sys/block") -> list:
    """List NVMe block devices via /sys/block/nvme*n*."""
    devs: list = []
    try:
        for name in sorted(os.listdir(sys_root)):
            if name.startswith("nvme") and "p" not in name:
                devs.append(name)
    except OSError:
        return []
    return devs


def read_block_stat(dev: str, sys_root: str = "/sys/block") -> Optional[dict]:
    """Parse /sys/block/<dev>/stat. Field 6 (0-indexed) is sectors written.
    Each sector = 512 bytes."""
    p = os.path.join(sys_root, dev, "stat")
    try:
        with open(p) as f:
            parts = f.read().split()
    except OSError:
        return None
    if len(parts) < 11:
        return None
    try:
        return {
            "reads_completed": int(parts[0]),
            "sectors_read": int(parts[2]),
            "writes_completed": int(parts[4]),
            "sectors_written": int(parts[6]),
        }
    except (ValueError, IndexError):
        return None


def bytes_written_total(dev: str, sys_root: str = "/sys/block") -> int:
    s = read_block_stat(dev, sys_root)
    if s is None:
        return 0
    return s["sectors_written"] * 512


def smartctl_data_units_written(device_path: str,
                                 timeout: float = 3.0) -> Optional[int]:
    """Return data_units_written (1 unit = 512000 bytes per NVMe spec) for
    an NVMe device. None if smartctl missing / parse fails."""
    if not shutil.which("smartctl"):
        return None
    try:
        r = subprocess.run(
            ["smartctl", "-A", "-j", device_path],
            capture_output=True, text=True, timeout=timeout,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired, OSError):
        return None
    if r.returncode not in (0, 2, 4):  # smartctl uses bitmask exit codes
        return None
    try:
        d = json.loads(r.stdout)
    except json.JSONDecodeError:
        return None
    nvme = d.get("nvme_smart_health_information_log") or {}
    duw = nvme.get("data_units_written")
    if isinstance(duw, int):
        return duw
    return None


def data_units_to_tb(duw: int) -> float:
    """NVMe spec : 1 data unit = 1000 × 512 = 512 000 bytes."""
    return duw * 512_000 / 1e12


_LAST: dict = {}  # {dev: (ts, sectors_written)}


def compute_write_rate(dev: str, sys_root: str = "/sys/block") -> Optional[float]:
    """Return bytes/sec written since last call. Returns None on first call."""
    s = read_block_stat(dev, sys_root)
    if s is None:
        return None
    now = time.time()
    prev = _LAST.get(dev)
    _LAST[dev] = (now, s["sectors_written"])
    if prev is None:
        return None
    dt = now - prev[0]
    if dt <= 0:
        return None
    return (s["sectors_written"] - prev[1]) * 512 / dt


def project_tbw_remaining(duw: Optional[int], rated_tbw: float,
                           write_rate_bps: Optional[float]) -> dict:
    """Given current data_units_written + drive's rated TBW + current write
    rate, project remaining days. Returns {used_tb, remaining_tb,
    days_remaining, pct_used}."""
    used_tb = data_units_to_tb(duw) if duw is not None else 0.0
    remaining_tb = max(0.0, rated_tbw - used_tb)
    pct_used = (used_tb / rated_tbw * 100) if rated_tbw > 0 else 0.0
    days_remaining = None
    if write_rate_bps and write_rate_bps > 0 and remaining_tb > 0:
        bytes_per_day = write_rate_bps * 86400
        days_remaining = remaining_tb * 1e12 / bytes_per_day
    return {
        "used_tb": round(used_tb, 3),
        "rated_tb": rated_tbw,
        "remaining_tb": round(remaining_tb, 3),
        "pct_used": round(pct_used, 1),
        "days_remaining": (round(days_remaining, 1)
                            if days_remaining is not None else None),
    }


def status(cfg=None) -> dict:
    """Aggregate snapshot for the UI."""
    rated_tbw = 600.0  # safe default; user can override via cfg
    if cfg:
        try:
            rated_tbw = float(cfg.get("NVME_RATED_TBW", "600"))
        except (ValueError, TypeError):
            pass
    procs = scan_llm_processes()
    total_swap = sum(p["swap_bytes"] for p in procs)
    devices: list = []
    for d in list_nvme_devices():
        rate = compute_write_rate(d)
        duw = smartctl_data_units_written(f"/dev/{d}")
        proj = project_tbw_remaining(duw, rated_tbw, rate)
        devices.append({
            "device": d,
            "write_rate_mibps": (round(rate / 1024 ** 2, 2)
                                  if rate is not None else None),
            "data_units_written": duw,
            "endurance": proj,
        })
    return {
        "ok": True,
        "llm_processes": procs,
        "llm_total_swap_bytes": total_swap,
        "llm_total_swap_gib": round(total_swap / 1024 ** 3, 2),
        "nvme_devices": devices,
        "warning": _diagnose(procs, devices),
    }


def _diagnose(procs: list, devices: list) -> Optional[str]:
    """Return a single human-readable warning if anything looks off."""
    total_swap = sum(p["swap_bytes"] for p in procs)
    if total_swap > 1 * 1024 ** 3:  # > 1 GiB swap by LLM procs
        return (f"LLM processes are using {total_swap / 1024 ** 3:.1f} GiB of swap. "
                "Reduce context length or unload models — every token re-reads "
                "swap pages from NVMe.")
    for d in devices:
        end = d.get("endurance") or {}
        days = end.get("days_remaining")
        if isinstance(days, (int, float)) and days < 365:
            return (f"/dev/{d['device']} : at current write rate, NVMe endurance "
                    f"runs out in ~{days:.0f} days.")
        if end.get("pct_used", 0) > 80:
            return (f"/dev/{d['device']} : {end['pct_used']:.0f}% of rated TBW used.")
    return None
