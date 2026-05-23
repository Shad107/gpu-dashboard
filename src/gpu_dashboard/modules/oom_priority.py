"""Module oom_priority — OOM-priority hardening for inference daemons (R&D #31.4).

Under memory pressure (e.g. someone triggered a big CPU job alongside
the inference workload), the Linux OOM-killer ranks processes by
`/proc/<pid>/oom_score`, which is heavily weighted by RSS. An LLM
runtime with a 16-GiB resident model hits the killer queue HARD —
often with `oom_score` of 800-1500, while system daemons sit at 100.

`/proc/<pid>/oom_score_adj` is the user's lever to shift that ranking
(range -1000 to 1000, lower = less likely to be killed). Default is 0,
which means the kernel sees no protection. For an inference daemon
that the user *wants* to survive memory pressure, setting
`OOMScoreAdjust=-500` in the systemd unit is the canonical fix.

This module walks /proc/<pid>/ for known inference daemons
(ollama/llama-server/vllm/...), reads oom_score + oom_score_adj,
and emits one of:

  - protected   adj <= -500 → safe (last to die)
  - hardened    -500 < adj < 0 → some protection but weak
  - default     adj == 0 → first to die (the headline catch — every
                fresh systemd-managed LLM daemon ships this way)
  - sacrificial adj > 0 → user has voluntarily made it more killable
                (rare, but a valid choice for batch/disposable workers)
  - unknown     cannot read sysfs/procfs

stdlib only.
"""
from __future__ import annotations

import os
from typing import Optional


NAME = "oom_priority"


_PROC = "/proc"


# Same heuristics as rlimit_audit + nvme_swap — match the same LLM
# runtimes. Kept local to avoid coupling.
LLM_COMM_PATTERNS = (
    "ollama", "llama-server", "llama_server", "llama.cpp", "llamacpp",
    "vllm", "sglang", "exllamav2", "exllama", "comfyui",
)
LLM_CMDLINE_HINTS = (
    "llama_cpp", "vllm.entrypoints", "ollama", "exllama",
    "text-generation-webui", "comfyui",
)


def _read(p: str) -> Optional[str]:
    try:
        with open(p) as f:
            return f.read()
    except OSError:
        return None


def read_comm(pid: int, proc_root: str = _PROC) -> str:
    t = _read(os.path.join(proc_root, str(pid), "comm"))
    return t.strip() if t else ""


def read_cmdline(pid: int, proc_root: str = _PROC) -> str:
    try:
        with open(os.path.join(proc_root, str(pid), "cmdline"), "rb") as f:
            return f.read().replace(b"\x00", b" ").decode("utf-8",
                                                            errors="replace")
    except OSError:
        return ""


def is_llm_proc(comm: str, cmdline: str) -> bool:
    low_comm = comm.lower()
    for pat in LLM_COMM_PATTERNS:
        if pat in low_comm:
            return True
    if low_comm.startswith("python") or low_comm.startswith("uvicorn"):
        for h in LLM_CMDLINE_HINTS:
            if h in cmdline:
                return True
    return False


def read_oom_score(pid: int, proc_root: str = _PROC) -> Optional[int]:
    t = _read(os.path.join(proc_root, str(pid), "oom_score"))
    if t is None:
        return None
    try:
        return int(t.strip())
    except ValueError:
        return None


def read_oom_score_adj(pid: int, proc_root: str = _PROC) -> Optional[int]:
    t = _read(os.path.join(proc_root, str(pid), "oom_score_adj"))
    if t is None:
        return None
    try:
        return int(t.strip())
    except ValueError:
        return None


def read_vm_rss_bytes(pid: int, proc_root: str = _PROC) -> Optional[int]:
    t = _read(os.path.join(proc_root, str(pid), "status"))
    if t is None:
        return None
    for line in t.splitlines():
        if line.startswith("VmRSS:"):
            parts = line.split()
            if len(parts) >= 2 and parts[1].isdigit():
                return int(parts[1]) * 1024
    return None


def _suggest_unit(comm: str) -> str:
    """Best-effort guess at the systemd unit name. Used in recipe text only;
    user can sed/replace if their unit is named differently."""
    base = comm.lower()
    for pat in ("ollama", "llama-server", "llama_server",
                 "llama.cpp", "vllm", "sglang", "comfyui"):
        if pat in base:
            return pat.replace("_", "-").replace(".", "-") + ".service"
    return f"{base}.service"


def classify(oom_score: Optional[int], oom_score_adj: Optional[int],
              comm: str = "") -> dict:
    if oom_score is None or oom_score_adj is None:
        return {"verdict": "unknown",
                "reason": "Cannot read /proc/<pid>/oom_score{,_adj}.",
                "recommendation": ""}
    if oom_score_adj <= -500:
        return {"verdict": "protected",
                "reason": (f"oom_score_adj={oom_score_adj} → among the last "
                           f"processes the OOM-killer will target."),
                "recommendation": ""}
    if oom_score_adj < 0:
        return {"verdict": "hardened",
                "reason": (f"oom_score_adj={oom_score_adj} provides some "
                           f"protection but stronger hardening (-500) is "
                           f"recommended for an inference daemon you want "
                           f"to survive memory pressure."),
                "recommendation": _recipe(comm)}
    if oom_score_adj == 0:
        return {"verdict": "default",
                "reason": (f"oom_score={oom_score} with oom_score_adj=0 "
                           f"(default) → this LLM daemon is among the "
                           f"FIRST to die when the kernel needs memory. "
                           f"Its high RSS makes it a prime target."),
                "recommendation": _recipe(comm)}
    return {"verdict": "sacrificial",
            "reason": (f"oom_score_adj={oom_score_adj} > 0 — you've "
                       f"voluntarily increased the kill priority. Valid "
                       f"for batch/disposable workers, but unusual."),
            "recommendation": ""}


def _recipe(comm: str) -> str:
    unit = _suggest_unit(comm)
    return (
        f"# Add to {unit} via systemd Drop-In:\n"
        f"sudo mkdir -p /etc/systemd/system/{unit}.d\n"
        f"sudo tee /etc/systemd/system/{unit}.d/oom.conf <<'EOF'\n"
        f"[Service]\n"
        f"OOMScoreAdjust=-500\n"
        f"EOF\n"
        f"sudo systemctl daemon-reload\n"
        f"sudo systemctl restart {unit}\n"
        f"# Verify: cat /proc/$(pidof -s {comm or 'PROC'})/oom_score_adj"
    )


_RANK = {
    "protected": 0,
    "sacrificial": 1,
    "unknown": 1,
    "hardened": 2,
    "default": 3,
}


def scan_llm_procs(proc_root: str = _PROC) -> list[dict]:
    out: list[dict] = []
    try:
        names = os.listdir(proc_root)
    except OSError:
        return out
    for n in names:
        if not n.isdigit():
            continue
        pid = int(n)
        comm = read_comm(pid, proc_root)
        cmdline = read_cmdline(pid, proc_root)
        if not is_llm_proc(comm, cmdline):
            continue
        out.append({
            "pid": pid,
            "comm": comm,
            "cmdline_short": cmdline[:140],
            "oom_score": read_oom_score(pid, proc_root),
            "oom_score_adj": read_oom_score_adj(pid, proc_root),
            "vm_rss_bytes": read_vm_rss_bytes(pid, proc_root),
        })
    return out


def status(cfg=None) -> dict:
    procs = scan_llm_procs(_PROC)
    if not procs:
        return {"ok": True, "process_count": 0, "processes": [],
                "worst_verdict": "no_llm_procs"}
    worst = "protected"
    enriched: list = []
    for p in procs:
        v = classify(p["oom_score"], p["oom_score_adj"], p["comm"])
        if _RANK.get(v["verdict"], 0) > _RANK.get(worst, 0):
            worst = v["verdict"]
        enriched.append({**p, "verdict": v, "recipe": v["recommendation"]})
    return {"ok": True, "process_count": len(enriched),
            "processes": enriched, "worst_verdict": worst}
