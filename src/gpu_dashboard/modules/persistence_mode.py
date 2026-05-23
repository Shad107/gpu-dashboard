"""Module persistence_mode — nvidia-persistenced state check (R&D #21.2).

NVIDIA's `nvidia-persistenced` daemon keeps the GPU initialized between
CUDA/NVML opens. When it's off, every fresh CUDA process pays a
~2-3 second cold-start tax — the driver tears down and re-initializes
the device each time the last process exits.

Most Linux distros (Debian, Ubuntu, Arch) ship the unit DISABLED out
of the box. Users running Ollama / vLLM / Triton notice the long
warm-up but rarely connect it to this knob.

This module reports :

  - persistence_mode per GPU (from nvidia-smi --query-gpu=persistence_mode)
  - daemon present/running (look at /var/run/nvidia-persistenced/socket)
  - copy-paste enable command

stdlib only.
"""
from __future__ import annotations

import os
import shutil
import subprocess
from typing import Optional


NAME = "persistence_mode"


DAEMON_SOCKET = "/var/run/nvidia-persistenced/socket"
DAEMON_PID = "/var/run/nvidia-persistenced/nvidia-persistenced.pid"


def daemon_socket_present(p: str = DAEMON_SOCKET) -> bool:
    return os.path.exists(p)


def daemon_pid(pid_path: str = DAEMON_PID) -> Optional[int]:
    try:
        with open(pid_path) as f:
            txt = f.read().strip()
        return int(txt) if txt.isdigit() else None
    except (OSError, ValueError):
        return None


def daemon_running() -> bool:
    """Return True iff /var/run/nvidia-persistenced/socket exists AND its
    pid file points to a live process."""
    if not daemon_socket_present():
        return False
    pid = daemon_pid()
    if pid is None:
        return True  # socket exists, pid file racing — count as running
    return os.path.exists(f"/proc/{pid}")


def per_gpu_persistence(timeout: float = 2.0) -> Optional[list[dict]]:
    """`nvidia-smi --query-gpu=index,name,persistence_mode`."""
    if not shutil.which("nvidia-smi"):
        return None
    try:
        r = subprocess.run(
            ["nvidia-smi",
             "--query-gpu=index,name,persistence_mode",
             "--format=csv,noheader,nounits"],
            capture_output=True, text=True, timeout=timeout,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired, OSError):
        return None
    if r.returncode != 0:
        return None
    out: list[dict] = []
    for line in r.stdout.strip().splitlines():
        parts = [p.strip() for p in line.split(",")]
        if len(parts) < 3 or not parts[0].isdigit():
            continue
        out.append({
            "index": int(parts[0]),
            "name": parts[1],
            "enabled": parts[2].lower() in ("enabled", "1", "on"),
            "raw": parts[2],
        })
    return out


def classify(daemon_up: bool, gpus: list[dict]) -> dict:
    """Return {verdict, reason, advisory}."""
    if not gpus:
        return {"verdict": "unknown",
                "reason": "nvidia-smi did not return any GPU.",
                "advisory": ""}
    any_off = any(not g["enabled"] for g in gpus)
    all_off = all(not g["enabled"] for g in gpus)
    if daemon_up and not any_off:
        return {"verdict": "ok",
                "reason": ("nvidia-persistenced daemon running, persistence "
                           "mode ON across all GPUs."),
                "advisory": ""}
    if daemon_up and any_off:
        # Daemon up but some GPU has it off — unusual
        return {"verdict": "partial",
                "reason": ("Daemon running but at least one GPU has "
                           "persistence mode OFF. Try `sudo nvidia-smi "
                           "-pm 1` to re-apply."),
                "advisory": "sudo nvidia-smi -pm 1"}
    if all_off:
        return {"verdict": "off",
                "reason": ("Persistence daemon is OFF and all GPUs run "
                           "without persistence. Every fresh CUDA "
                           "process pays ~2-3 s cold-start tax. Enable "
                           "the daemon to remove it."),
                "advisory": ("sudo systemctl enable --now "
                              "nvidia-persistenced.service")}
    # Daemon off but some GPUs on (probably set via nvidia-smi -pm 1)
    return {"verdict": "off_with_manual",
            "reason": ("Persistence daemon OFF but some GPUs report "
                       "persistence ON (manual nvidia-smi -pm 1 ?). "
                       "This survives until reboot only."),
            "advisory": ("sudo systemctl enable --now "
                          "nvidia-persistenced.service")}


def status(cfg=None) -> dict:
    """Aggregate snapshot."""
    daemon_up = daemon_running()
    gpus = per_gpu_persistence()
    if gpus is None:
        return {
            "ok": False,
            "reason": "nvidia-smi unreachable",
            "daemon_running": daemon_up,
            "gpus": [],
        }
    return {
        "ok": True,
        "daemon_running": daemon_up,
        "daemon_socket": DAEMON_SOCKET,
        "daemon_pid": daemon_pid(),
        "gpus": gpus,
        "verdict": classify(daemon_up, gpus),
    }
