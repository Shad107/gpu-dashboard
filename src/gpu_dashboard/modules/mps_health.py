"""Module mps_health — CUDA Multi-Process Service health probe (R&D #19.6).

CUDA MPS lets multiple processes share one GPU's compute units. It is
the right answer for hosting several small LLMs at once, but its
failure modes are silent — a hung mps-server hangs every CUDA client
behind it. Most users never know it exists, let alone how to debug it.

This module :

  1. Checks whether the MPS control daemon socket directory exists
     (default /tmp/nvidia-mps, overridable via CUDA_MPS_PIPE_DIRECTORY)
  2. Looks for `nvidia-cuda-mps-server` processes
  3. Pipes `get_server_list` + `get_client_list` to nvidia-cuda-mps-control
     to enumerate active clients and their SM-share allocations
  4. Returns a verdict : "not configured" / "running" / "stalled"

Specific stalled-state detection : control socket exists but every
`get_*_list` returns an empty list AND the server pid is unresponsive
to a 1-second timeout.

stdlib only : subprocess + os.
"""
from __future__ import annotations

import os
import re
import shutil
import subprocess
from typing import Optional


NAME = "mps_health"


DEFAULT_PIPE_DIR = "/tmp/nvidia-mps"
DEFAULT_LOG_DIR = "/var/log/nvidia-mps"


def pipe_dir() -> str:
    return os.environ.get("CUDA_MPS_PIPE_DIRECTORY", DEFAULT_PIPE_DIR)


def log_dir() -> str:
    return os.environ.get("CUDA_MPS_LOG_DIRECTORY", DEFAULT_LOG_DIR)


def has_control_binary() -> bool:
    return shutil.which("nvidia-cuda-mps-control") is not None


def control_socket_exists(p_dir: Optional[str] = None) -> bool:
    """The control socket is named `control` inside the pipe directory."""
    d = p_dir or pipe_dir()
    return os.path.exists(os.path.join(d, "control"))


def find_mps_server_pids(proc_root: str = "/proc") -> list[int]:
    """Scan /proc/*/comm for nvidia-cuda-mps-server."""
    out: list[int] = []
    try:
        names = os.listdir(proc_root)
    except OSError:
        return out
    for name in names:
        if not name.isdigit():
            continue
        try:
            with open(os.path.join(proc_root, name, "comm")) as f:
                comm = f.read().strip()
        except OSError:
            continue
        # comm is truncated to 15 chars : "nvidia-cuda-mps"
        if comm.startswith("nvidia-cuda-mps"):
            out.append(int(name))
    return out


def _talk_to_control(commands: list[str], timeout: float = 1.5) -> Optional[str]:
    """Pipe newline-separated commands to nvidia-cuda-mps-control's stdin.
    Returns stdout or None on failure / timeout."""
    if not has_control_binary():
        return None
    stdin = ("\n".join(commands) + "\nquit\n").encode()
    try:
        r = subprocess.run(
            ["nvidia-cuda-mps-control"],
            input=stdin, capture_output=True, timeout=timeout,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired, OSError):
        return None
    if r.returncode != 0:
        return None
    return r.stdout.decode("utf-8", errors="replace")


def parse_server_list(output: str) -> list[dict]:
    """Parse the multi-line response from `get_server_list`.
    Format roughly: each line is '<pid>' on its own."""
    out: list[dict] = []
    for line in output.splitlines():
        s = line.strip()
        if s.isdigit():
            out.append({"pid": int(s)})
    return out


def parse_client_list(output: str) -> list[dict]:
    """Parse the response from `get_client_list`. Each line typically:
    '<client_pid> <uid> <name>'."""
    out: list[dict] = []
    for line in output.splitlines():
        parts = line.strip().split()
        if not parts or not parts[0].isdigit():
            continue
        rec: dict = {"pid": int(parts[0])}
        if len(parts) > 1 and parts[1].isdigit():
            rec["uid"] = int(parts[1])
        if len(parts) > 2:
            rec["name"] = " ".join(parts[2:])
        out.append(rec)
    return out


def parse_active_thread_percentage(output: str) -> Optional[float]:
    """Parse `get_default_active_thread_percentage` response."""
    m = re.search(r"(\d+(?:\.\d+)?)\s*%?", output)
    if not m:
        return None
    try:
        return float(m.group(1))
    except ValueError:
        return None


def status(cfg=None) -> dict:
    """Aggregate health snapshot."""
    p_dir = pipe_dir()
    socket_present = control_socket_exists(p_dir)
    server_pids = find_mps_server_pids()
    control_avail = has_control_binary()

    if not control_avail and not socket_present and not server_pids:
        return _shape("not_configured",
                      "MPS not installed on this system (no control binary, "
                      "no socket, no server). Nothing to do.",
                      p_dir, server_pids, [],
                      socket_present, control_avail, None)

    if not socket_present and not server_pids:
        return _shape("not_running",
                      "nvidia-cuda-mps-control binary is present but MPS is "
                      "not running. Start with `nvidia-cuda-mps-control -d`.",
                      p_dir, server_pids, [],
                      socket_present, control_avail, None)

    # Try to enumerate clients + SM share
    servers_raw = _talk_to_control(["get_server_list"])
    clients_raw = _talk_to_control(["get_client_list"])
    sm_share_raw = _talk_to_control(["get_default_active_thread_percentage"])
    sm_share = (parse_active_thread_percentage(sm_share_raw)
                if sm_share_raw else None)
    clients = parse_client_list(clients_raw) if clients_raw else []

    # Stalled = socket exists, server pid alive, but control timeouts
    if socket_present and server_pids and servers_raw is None:
        return _shape("stalled",
                      f"MPS server (pid {server_pids[0]}) is unresponsive — "
                      "control socket exists but nvidia-cuda-mps-control "
                      "timed out. Restart : `echo quit | "
                      "nvidia-cuda-mps-control` then `nvidia-cuda-mps-control "
                      "-d`.",
                      p_dir, server_pids, [],
                      socket_present, control_avail, sm_share)

    return _shape("running",
                  (f"MPS healthy. {len(clients)} client(s) attached, "
                   f"default SM share = {sm_share}% per client."
                   if sm_share is not None else
                   f"MPS running with {len(clients)} client(s)."),
                  p_dir, server_pids, clients,
                  socket_present, control_avail, sm_share)


def _shape(state: str, advice: str, p_dir: str,
            server_pids: list[int], clients: list[dict],
            socket_present: bool, control_avail: bool,
            sm_share: Optional[float]) -> dict:
    return {
        "ok": True,
        "state": state,
        "pipe_dir": p_dir,
        "control_socket_present": socket_present,
        "control_binary_available": control_avail,
        "server_pids": server_pids,
        "clients": clients,
        "default_sm_share_pct": sm_share,
        "advice": advice,
    }
