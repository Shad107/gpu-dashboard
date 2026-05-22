"""Module jupyter_monitor — discover running Jupyter kernels + attribute GPU
usage to notebooks (R&D #8.7).

Solves the 'who's hogging the GPU' problem on shared lab boxes.

Discovery :
  ~/.local/share/jupyter/runtime/kernel-*.json — one file per running kernel
  Each file contains {ip, transport, signature_scheme, key, shell_port,
                      iopub_port, stdin_port, hb_port, control_port,
                      kernel_name, pid (since jupyter_client 8.x)}

  Some older jupyter_client versions don't write the pid into the file.
  We fall back to lsof on the kernel's ports to find the PID.

Cross-reference with nvidia-smi pmon to attribute VRAM + SM% per kernel.

stdlib only. No jupyter_client dep.
"""
from __future__ import annotations

import glob
import json
import os
import subprocess
from typing import Optional


NAME = "jupyter_monitor"


def runtime_dir() -> str:
    """Default Jupyter runtime dir on Linux."""
    return os.path.expanduser("~/.local/share/jupyter/runtime")


def find_kernel_files(rt_dir: Optional[str] = None) -> list:
    """Return list of kernel-*.json filepaths."""
    rt = rt_dir or runtime_dir()
    return sorted(glob.glob(os.path.join(rt, "kernel-*.json")))


def parse_kernel_file(path: str) -> Optional[dict]:
    """Parse one kernel-*.json. Returns dict with at least {file, kernel_name}."""
    try:
        with open(path) as f:
            data = json.load(f)
    except (OSError, json.JSONDecodeError):
        return None
    if not isinstance(data, dict):
        return None
    return {
        "file": os.path.basename(path),
        "kernel_id": os.path.basename(path).removeprefix("kernel-").removesuffix(".json"),
        "kernel_name": data.get("kernel_name", "python3"),
        "pid": data.get("pid"),
        "shell_port": data.get("shell_port"),
        "ip": data.get("ip", "127.0.0.1"),
    }


def find_pid_by_port(port: int) -> Optional[int]:
    """Run lsof to find which PID owns a TCP port. Returns None if no match."""
    if not port:
        return None
    try:
        r = subprocess.run(
            ["lsof", "-i", f":{port}", "-sTCP:LISTEN", "-t", "-n", "-P"],
            capture_output=True, text=True, timeout=2,
        )
        if r.returncode == 0 and r.stdout.strip():
            return int(r.stdout.strip().splitlines()[0])
    except (FileNotFoundError, subprocess.SubprocessError, ValueError, OSError):
        pass
    return None


def get_pmon_by_pid() -> dict:
    """Return {pid: {sm_pct, vram_mib, command}} from nvidia-smi pmon."""
    try:
        r = subprocess.run(
            ["nvidia-smi", "pmon", "-c", "1", "-s", "um"],
            capture_output=True, text=True, timeout=2,
        )
    except (FileNotFoundError, subprocess.SubprocessError, OSError):
        return {}
    if r.returncode != 0:
        return {}
    out: dict = {}
    for line in r.stdout.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        # Columns : gpu pid type sm mem enc dec jpg ofa fb ccpm command
        parts = line.split()
        if len(parts) < 11:
            continue
        try:
            pid = int(parts[1])
        except ValueError:
            continue
        try:
            sm = float(parts[3])
        except ValueError:
            sm = 0.0
        try:
            vram = int(parts[9])
        except ValueError:
            vram = 0
        cmd = parts[-1] if len(parts) > 11 else ""
        out[pid] = {"sm_pct": sm, "vram_mib": vram, "command": cmd}
    return out


def get_notebook_path_for_pid(pid: int) -> Optional[str]:
    """Try to extract the notebook file path from /proc/<pid>/cmdline.

    Jupyter kernels typically run as : python -m ipykernel_launcher -f /path/kernel.json
    The notebook file isn't always directly in cmdline ; for now return
    cmdline as a best-effort identifier.
    """
    try:
        with open(f"/proc/{pid}/cmdline") as f:
            cmdline = f.read().replace("\0", " ").strip()
        return cmdline if cmdline else None
    except OSError:
        return None


def list_kernels(rt_dir: Optional[str] = None) -> list:
    """Discover all running kernels with GPU attribution.

    Returns list of dicts :
      {kernel_id, kernel_name, pid, sm_pct, vram_mib, command, cmdline}
    sorted by vram_mib desc (top consumer first).
    """
    files = find_kernel_files(rt_dir)
    if not files:
        return []
    pmon = get_pmon_by_pid()
    kernels: list = []
    for path in files:
        info = parse_kernel_file(path)
        if not info:
            continue
        # Resolve PID if file didn't include one
        if not info.get("pid"):
            info["pid"] = find_pid_by_port(info.get("shell_port"))
        pid = info.get("pid")
        gpu_use = pmon.get(pid, {}) if pid else {}
        info["sm_pct"] = gpu_use.get("sm_pct", 0.0)
        info["vram_mib"] = gpu_use.get("vram_mib", 0)
        info["command"] = gpu_use.get("command", "")
        info["cmdline"] = get_notebook_path_for_pid(pid) if pid else None
        info["on_gpu"] = info["vram_mib"] > 0 or info["sm_pct"] > 0
        kernels.append(info)
    kernels.sort(key=lambda k: k["vram_mib"], reverse=True)
    return kernels
