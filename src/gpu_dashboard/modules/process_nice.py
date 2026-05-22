"""Module process_nice — GPU process nice/ionice advisor (R&D #19.1).

On mixed-workload boxes (gaming + LLM + Blender on the same GPU), all
processes default to nice 0 — so a background CUDA compile gets the
same priority as your active game frame. This module reads the
compute-process list from nvidia-smi, joins it with /proc/<pid>/stat
to extract the nice / ionice values, classifies each process by
intent, and recommends renice / ionice tweaks the user can apply
(via shell — never automatic, since the daemon doesn't run as root).

Classes :
  - interactive    (game launcher, browser game) → keep nice 0
  - llm_serve      (Ollama / llama-server / vLLM) → nice +5
  - llm_train      (python with deepspeed/accelerate/torch) → nice +10
  - render         (blender, comfyui, automatic1111) → nice +15
  - encode         (ffmpeg, OBS, handbrake) → nice -5 (foreground feel)
  - unknown        → no recommendation

stdlib only.
"""
from __future__ import annotations

import os
import shutil
import subprocess
from typing import Optional


NAME = "process_nice"


# Patterns checked against /proc/<pid>/comm + cmdline.
# Each tuple : (class_name, suggested_nice, comm_substr, cmdline_hint).
CLASSIFIERS = [
    ("interactive", 0,
     ("steam", "lutris", "heroic", "wine", "gamescope"),
     ()),
    ("llm_serve", 5,
     ("ollama", "llama-server", "llama-cli"),
     ("vllm.entrypoints", "sglang")),
    ("llm_train", 10,
     (),
     ("deepspeed", "accelerate launch", "torch.distributed", "lightning",
      "transformers Trainer")),
    ("render", 15,
     ("blender", "comfyui"),
     ("automatic1111", "stable-diffusion-webui", "InvokeAI", "Fooocus")),
    ("encode", -5,
     ("ffmpeg", "HandBrake", "obs"),
     ()),
]


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


def _read_stat(pid: int, proc_root: str = "/proc") -> Optional[dict]:
    """Parse /proc/<pid>/stat. Returns {nice, priority, state, pgrp}.

    The 'comm' field is wrapped in parens and may contain spaces, so
    we split carefully.
    """
    p = os.path.join(proc_root, str(pid), "stat")
    try:
        with open(p) as f:
            raw = f.read()
    except OSError:
        return None
    # Find the last ')' — everything before is "pid (comm" possibly with spaces
    rp = raw.rfind(")")
    if rp == -1:
        return None
    rest = raw[rp + 1 :].strip().split()
    # After comm, fields start at index 0 of rest = field 3 in proc(5) (state)
    # field 18 in proc(5) = priority ; field 19 = nice
    # rest[0] = state, rest[1] = ppid, rest[15] = priority, rest[16] = nice
    if len(rest) < 17:
        return None
    try:
        return {
            "state": rest[0],
            "priority": int(rest[15]),
            "nice": int(rest[16]),
            "pgrp": int(rest[3]) if rest[3].lstrip("-").isdigit() else None,
        }
    except (ValueError, IndexError):
        return None


def classify(comm: str, cmdline: str) -> tuple[str, int]:
    """Return (class_name, suggested_nice)."""
    low_comm = comm.lower()
    low_cmd = cmdline.lower()
    for cname, sug_nice, comms, hints in CLASSIFIERS:
        for c in comms:
            if c in low_comm:
                return cname, sug_nice
        for h in hints:
            if h.lower() in low_cmd:
                return cname, sug_nice
    return "unknown", 0


def list_gpu_compute_pids(timeout: float = 2.0) -> list[int]:
    """`nvidia-smi --query-compute-apps=pid` → list of integer PIDs."""
    if not shutil.which("nvidia-smi"):
        return []
    try:
        r = subprocess.run(
            ["nvidia-smi", "--query-compute-apps=pid",
             "--format=csv,noheader,nounits"],
            capture_output=True, text=True, timeout=timeout,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired, OSError):
        return []
    if r.returncode != 0:
        return []
    out: list[int] = []
    for line in r.stdout.splitlines():
        s = line.strip()
        if s.isdigit():
            out.append(int(s))
    return out


def advise_process(pid: int, proc_root: str = "/proc") -> Optional[dict]:
    """Build a single advisor record for one PID."""
    comm = _read_comm(pid, proc_root)
    if not comm:
        return None
    cmdline = _read_cmdline(pid, proc_root)
    stat = _read_stat(pid, proc_root)
    cls, suggested = classify(comm, cmdline)
    current_nice = stat["nice"] if stat else None
    needs_change = (current_nice is not None
                    and cls != "unknown"
                    and current_nice != suggested)
    return {
        "pid": pid,
        "comm": comm,
        "cmdline_short": cmdline[:120],
        "class": cls,
        "current_nice": current_nice,
        "suggested_nice": suggested if cls != "unknown" else None,
        "needs_change": needs_change,
        "shell_command": (f"sudo renice -n {suggested} -p {pid}"
                          if needs_change else None),
    }


def status(cfg=None) -> dict:
    """Aggregate snapshot."""
    pids = list_gpu_compute_pids()
    advisors: list[dict] = []
    for pid in pids:
        rec = advise_process(pid)
        if rec is not None:
            advisors.append(rec)
    if not pids:
        return {
            "ok": True,
            "reason": "no GPU compute processes detected",
            "processes": [],
            "needs_action_count": 0,
        }
    return {
        "ok": True,
        "processes": advisors,
        "needs_action_count": sum(1 for a in advisors if a["needs_change"]),
    }
