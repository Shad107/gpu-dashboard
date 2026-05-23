"""Module cuda_ctx_leak — Zombie CUDA-FD detector (R&D #26.2).

When a Jupyter kernel or llama-server crashes mid-request, the
process may exit but its libcuda fd on /dev/nvidia0 stays held by
a parent or sibling (think : python repl, supervising shell,
systemd-user-app frame). nvidia-smi --query-compute-apps doesn't
list the dead PID anymore — but the VRAM it allocated stays pinned
until the FD is closed.

Symptom : "I closed my notebook but `nvidia-smi` still shows 18 GB
used".

This module enumerates /proc/<pid>/fd/* across all PIDs, finds
those that have one or more /dev/nvidia* (excluding /dev/nvidiactl,
/dev/nvidia-uvm and other control nodes — those are valid for
running CUDA processes), then cross-references against nvidia-smi
--query-compute-apps. Anything *not* in the compute-app list but
still holding a device fd is a leak candidate.

Read-only. Suggests `kill -TERM <pid>` per candidate but never
acts.

stdlib only.
"""
from __future__ import annotations

import os
import re
import shutil
import subprocess
from typing import Optional


NAME = "cuda_ctx_leak"


_DEV_NVIDIA_RE = re.compile(r"^/dev/nvidia\d+$")
_CTL_NODES = {"/dev/nvidiactl", "/dev/nvidia-uvm", "/dev/nvidia-uvm-tools",
              "/dev/nvidia-caps", "/dev/nvidia-modeset"}


def scan_proc_for_cuda_fds(proc_root: str = "/proc") -> dict[int, list[str]]:
    """Return {pid: [/dev/nvidiaN, ...]} for every PID holding a
    /dev/nvidiaN FD (excluding control nodes).

    Walks /proc/<pid>/fd/. Permission errors on other users' PIDs
    are swallowed (they show up as opaque). Caller can then say
    'and there are N PIDs we cannot inspect'."""
    out: dict[int, list[str]] = {}
    try:
        names = os.listdir(proc_root)
    except OSError:
        return out
    for name in names:
        if not name.isdigit():
            continue
        pid = int(name)
        fd_dir = os.path.join(proc_root, name, "fd")
        try:
            entries = os.listdir(fd_dir)
        except (OSError, PermissionError):
            continue
        nvids: list[str] = []
        for fd_name in entries:
            link = os.path.join(fd_dir, fd_name)
            try:
                target = os.readlink(link)
            except OSError:
                continue
            # /dev/nvidiaN — device node ; skip control nodes
            if _DEV_NVIDIA_RE.match(target):
                nvids.append(target)
            elif target in _CTL_NODES:
                # Control node alone doesn't pin VRAM ; ignore
                continue
        if nvids:
            out[pid] = sorted(set(nvids))
    return out


def list_compute_pids(timeout: float = 2.0) -> set[int]:
    """nvidia-smi --query-compute-apps=pid. Returns set."""
    if not shutil.which("nvidia-smi"):
        return set()
    try:
        r = subprocess.run(
            ["nvidia-smi", "--query-compute-apps=pid",
             "--format=csv,noheader,nounits"],
            capture_output=True, text=True, timeout=timeout,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired, OSError):
        return set()
    if r.returncode != 0:
        return set()
    out: set[int] = set()
    for line in r.stdout.splitlines():
        s = line.strip()
        if s.isdigit():
            out.add(int(s))
    return out


def read_comm(pid: int, proc_root: str = "/proc") -> str:
    try:
        with open(os.path.join(proc_root, str(pid), "comm")) as f:
            return f.read().strip()
    except OSError:
        return ""


def read_cmdline_short(pid: int, proc_root: str = "/proc",
                       max_chars: int = 120) -> str:
    try:
        with open(os.path.join(proc_root, str(pid), "cmdline"), "rb") as f:
            raw = f.read()
        s = raw.replace(b"\x00", b" ").decode("utf-8", errors="replace")
        return s.strip()[:max_chars]
    except OSError:
        return ""


def find_leaks(fd_holders: dict[int, list[str]],
                compute_pids: set[int],
                proc_root: str = "/proc") -> list[dict]:
    """Cross-reference. PIDs holding /dev/nvidiaN but NOT in the
    compute-app list are leak candidates."""
    out: list[dict] = []
    for pid, devices in fd_holders.items():
        if pid in compute_pids:
            continue
        out.append({
            "pid": pid,
            "comm": read_comm(pid, proc_root),
            "cmdline_short": read_cmdline_short(pid, proc_root),
            "devices": devices,
            "kill_cmd": f"kill -TERM {pid}",
        })
    return out


def classify(fd_holders: dict, compute_pids: set, leaks: list[dict]) -> dict:
    """Headline verdict."""
    if not fd_holders:
        return {"verdict": "no_fds",
                "reason": ("No process is holding a /dev/nvidiaN device "
                           "node. Either CUDA isn't running or we don't "
                           "have permission to read other users' /proc.")}
    if not leaks:
        return {"verdict": "ok",
                "reason": (f"All {len(fd_holders)} PIDs holding a "
                           "/dev/nvidiaN FD are also in nvidia-smi's "
                           "compute-app list. No zombies detected.")}
    total_leaked_devs = sum(len(l["devices"]) for l in leaks)
    return {"verdict": "leaks_detected",
            "reason": (f"{len(leaks)} process(es) hold {total_leaked_devs} "
                       "CUDA FD(s) but are not in nvidia-smi's compute-app "
                       "list. VRAM stays pinned until those FDs close — "
                       "kill them or wait for the parent to exit.")}


def status(cfg=None) -> dict:
    """Aggregate snapshot."""
    fd_holders = scan_proc_for_cuda_fds()
    compute_pids = list_compute_pids()
    leaks = find_leaks(fd_holders, compute_pids)
    verdict = classify(fd_holders, compute_pids, leaks)
    return {
        "ok": True,
        "fd_holder_pids": sorted(fd_holders.keys()),
        "fd_holder_count": len(fd_holders),
        "compute_pids": sorted(compute_pids),
        "compute_pid_count": len(compute_pids),
        "leaks": leaks,
        "leak_count": len(leaks),
        "verdict": verdict,
    }
