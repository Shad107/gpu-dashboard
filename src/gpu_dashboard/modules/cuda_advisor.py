"""Module cuda_advisor — CUDA_VISIBLE_DEVICES UUID drift detector (R&D #18.3).

After an NVIDIA driver upgrade or a PCIe reorder, the kernel can rename
GPU indices. A long-running process that pinned `CUDA_VISIBLE_DEVICES=0`
in its environment will silently keep targeting "index 0" — but that may
no longer be the same physical GPU it was launched against. CUDA jobs
that should hit a 3090 might end up on an iGPU or a different card.

This module :

  1. Reads each process's CUDA_VISIBLE_DEVICES from /proc/<pid>/environ
  2. Looks up the current index→UUID map from `nvidia-smi`
  3. For each entry in the env var :
       • UUID form ("GPU-xxxx…") → must match a live UUID
       • integer index ("0", "1,2") → resolves to current index→UUID
  4. Flags processes whose pinned target no longer matches reality

stdlib only.
"""
from __future__ import annotations

import os
import re
import subprocess
from typing import Iterable, Optional


NAME = "cuda_advisor"


def list_gpu_uuids() -> list[dict]:
    """Return [{index, uuid, name}, ...] for currently visible GPUs.
    Empty list if nvidia-smi missing or returns nothing."""
    try:
        r = subprocess.run(
            ["nvidia-smi", "--query-gpu=index,uuid,name",
             "--format=csv,noheader,nounits"],
            capture_output=True, text=True, timeout=2.0,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired, OSError):
        return []
    if r.returncode != 0:
        return []
    out: list[dict] = []
    for line in r.stdout.splitlines():
        parts = [p.strip() for p in line.split(",")]
        if len(parts) < 3 or not parts[0].isdigit():
            continue
        out.append({"index": int(parts[0]), "uuid": parts[1], "name": parts[2]})
    return out


def parse_cuda_env(value: str) -> list[str]:
    """Parse a CUDA_VISIBLE_DEVICES env value. Returns list of entries
    in original form (integer or UUID-string). Empty for empty / invalid."""
    if not value:
        return []
    entries: list[str] = []
    for raw in value.split(","):
        s = raw.strip()
        if not s:
            continue
        entries.append(s)
    return entries


def _is_uuid_form(s: str) -> bool:
    return s.startswith("GPU-") or s.startswith("MIG-")


def resolve_entry(entry: str, gpus: list[dict]) -> Optional[dict]:
    """Resolve a single CUDA_VISIBLE_DEVICES entry against the live
    index→UUID map. Returns the matched GPU dict, or None if unresolved."""
    if _is_uuid_form(entry):
        for g in gpus:
            if g["uuid"] == entry or g["uuid"].startswith(entry):
                return g
        return None
    if entry.isdigit():
        idx = int(entry)
        for g in gpus:
            if g["index"] == idx:
                return g
    return None


_ENV_RE = re.compile(rb"(?:^|\x00)CUDA_VISIBLE_DEVICES=([^\x00]*)")


def read_proc_env(pid: int, proc_root: str = "/proc") -> Optional[str]:
    """Return the CUDA_VISIBLE_DEVICES value for <proc_root>/<pid>, or
    None if not set / unreadable."""
    p = os.path.join(proc_root, str(pid), "environ")
    try:
        with open(p, "rb") as f:
            raw = f.read(64 * 1024)
    except (OSError, PermissionError):
        return None
    m = _ENV_RE.search(b"\x00" + raw)
    if not m:
        return None
    try:
        return m.group(1).decode("utf-8", errors="replace")
    except Exception:
        return None


def read_proc_comm(pid: int, proc_root: str = "/proc") -> str:
    try:
        with open(os.path.join(proc_root, str(pid), "comm")) as f:
            return f.read().strip()
    except OSError:
        return ""


def _iter_pids(proc_root: str = "/proc") -> Iterable[int]:
    try:
        for name in os.listdir(proc_root):
            if name.isdigit():
                yield int(name)
    except OSError:
        return


def scan_processes(gpus: Optional[list[dict]] = None,
                    proc_root: str = "/proc") -> list[dict]:
    """Scan all visible PIDs. Returns a list of process records that have
    CUDA_VISIBLE_DEVICES set, each with resolved targets and drift flags.

    Each record: {
      pid: int, comm: str, raw: str, entries: [str],
      resolved: [{entry: str, gpu: {index, uuid, name} | None,
                  drift: bool, reason: str}],
      has_drift: bool,
    }
    """
    if gpus is None:
        gpus = list_gpu_uuids()
    out: list[dict] = []
    for pid in _iter_pids(proc_root):
        raw = read_proc_env(pid, proc_root)
        if raw is None:
            continue
        entries = parse_cuda_env(raw)
        if not entries:
            continue
        resolved: list[dict] = []
        any_drift = False
        for e in entries:
            g = resolve_entry(e, gpus)
            if g is None:
                drift = True
                reason = ("UUID not found" if _is_uuid_form(e)
                          else "index out of range")
            else:
                drift = False
                reason = "ok"
            resolved.append({"entry": e, "gpu": g,
                              "drift": drift, "reason": reason})
            if drift:
                any_drift = True
        out.append({
            "pid": pid,
            "comm": read_proc_comm(pid, proc_root),
            "raw": raw,
            "entries": entries,
            "resolved": resolved,
            "has_drift": any_drift,
        })
    return out


def status(cfg=None) -> dict:
    """Aggregate status for the UI."""
    gpus = list_gpu_uuids()
    procs = scan_processes(gpus)
    drifters = [p for p in procs if p["has_drift"]]
    return {
        "ok": True,
        "gpus": gpus,
        "gpu_count": len(gpus),
        "process_count": len(procs),
        "drift_count": len(drifters),
        "processes": procs,
        "recommendation": _recommend(gpus, procs),
    }


def _recommend(gpus: list[dict], procs: list[dict]) -> str:
    if not gpus:
        return "nvidia-smi unreachable — cannot audit CUDA_VISIBLE_DEVICES."
    drifters = [p for p in procs if p["has_drift"]]
    if drifters:
        return (f"{len(drifters)} process(es) target stale GPU indices / UUIDs. "
                "Restart these processes with up-to-date "
                "CUDA_VISIBLE_DEVICES (prefer UUID form, GPU-…).")
    if not procs:
        return "No CUDA_VISIBLE_DEVICES set in any process — fine."
    return ("All CUDA_VISIBLE_DEVICES values resolve cleanly. "
            "Tip : prefer UUID form for stability across driver upgrades.")
