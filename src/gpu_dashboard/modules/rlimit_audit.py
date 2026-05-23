"""Module rlimit_audit — rlimit auditor for LLM daemons (R&D #29.8).

Users routinely launch llama-server / Ollama / vLLM with `--mlock`
to pin model weights in RAM. systemd-launched daemons inherit
`LimitMEMLOCK=64K` from the service template — far below the
~10 GB of a typical quantized model. The result : `mlock()` fails
silently, weights spill to swap, inference slows to a crawl. The
user blames "the SSD" or "Linux paging".

This module walks /proc/<pid>/{comm,cmdline,status,limits} for
processes that look like LLM runtimes, extracts the
`Max locked memory` rlimit, and compares against VmLck. Verdicts :

  - ok            (no LLM daemon found, OR all limits ≥ 1 GiB)
  - low_limit     (LLM daemon found with MEMLOCK < 1 GiB)
  - severely_low  (MEMLOCK < 64 MiB AND process is using mlock)
  - unknown       (cannot read /proc/<pid>/limits)

Emits the systemd Drop-In to remove the limit :

  [Service]
  LimitMEMLOCK=infinity

stdlib only.
"""
from __future__ import annotations

import os
import re
from typing import Optional


NAME = "rlimit_audit"


# Same heuristics as #18.1 (nvme_swap) — we want to catch the
# same LLM runtimes.
LLM_COMM_PATTERNS = (
    "ollama", "llama-server", "llama_server", "llama.cpp", "llamacpp",
    "vllm", "sglang", "exllamav2", "exllama",
)
LLM_CMDLINE_HINTS = (
    "llama_cpp", "vllm.entrypoints", "ollama", "exllama",
    "text-generation-webui",
)


def _read(p: str) -> Optional[str]:
    try:
        with open(p) as f:
            return f.read()
    except OSError:
        return None


def read_comm(pid: int, proc_root: str = "/proc") -> str:
    txt = _read(os.path.join(proc_root, str(pid), "comm"))
    return txt.strip() if txt else ""


def read_cmdline(pid: int, proc_root: str = "/proc") -> str:
    try:
        with open(os.path.join(proc_root, str(pid), "cmdline"), "rb") as f:
            return f.read().replace(b"\x00", b" ").decode(
                "utf-8", errors="replace")
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


def read_memlock_rlimit(pid: int, proc_root: str = "/proc") -> Optional[int]:
    """Parse /proc/<pid>/limits → Max locked memory soft limit, in bytes.
    'unlimited' → returns 2**63-1 sentinel."""
    txt = _read(os.path.join(proc_root, str(pid), "limits"))
    if txt is None:
        return None
    for line in txt.splitlines():
        if not line.lower().startswith("max locked memory"):
            continue
        # Columns: name<padding>Soft<padding>Hard<padding>Units
        # 'Max locked memory    65536    65536    bytes'
        # or 'Max locked memory    unlimited    unlimited    bytes'
        m = re.match(r"max locked memory\s+(\S+)\s+(\S+)\s+(\S+)",
                       line.lower())
        if not m:
            continue
        soft = m.group(1)
        if soft == "unlimited":
            return 2 ** 63 - 1
        try:
            return int(soft)
        except ValueError:
            return None
    return None


def read_vm_lck_bytes(pid: int, proc_root: str = "/proc") -> Optional[int]:
    """Parse VmLck from /proc/<pid>/status, in bytes."""
    txt = _read(os.path.join(proc_root, str(pid), "status"))
    if txt is None:
        return None
    for line in txt.splitlines():
        if line.startswith("VmLck:"):
            parts = line.split()
            if len(parts) >= 2 and parts[1].isdigit():
                return int(parts[1]) * 1024
    return None


def scan_llm_procs(proc_root: str = "/proc") -> list[dict]:
    """Return per-LLM-process records with rlimit + VmLck."""
    out: list[dict] = []
    try:
        names = os.listdir(proc_root)
    except OSError:
        return out
    for name in names:
        if not name.isdigit():
            continue
        pid = int(name)
        comm = read_comm(pid, proc_root)
        cmdline = read_cmdline(pid, proc_root)
        if not is_llm_proc(comm, cmdline):
            continue
        memlock = read_memlock_rlimit(pid, proc_root)
        vm_lck = read_vm_lck_bytes(pid, proc_root)
        out.append({
            "pid": pid,
            "comm": comm,
            "cmdline_short": cmdline[:140],
            "memlock_bytes": memlock,
            "vm_lck_bytes": vm_lck,
        })
    return out


def classify(rec: dict) -> dict:
    """Per-process verdict + recommendation."""
    memlock = rec.get("memlock_bytes")
    vm_lck = rec.get("vm_lck_bytes") or 0
    if memlock is None:
        return {"verdict": "unknown",
                "reason": "Could not read /proc/<pid>/limits.",
                "recommendation": ""}
    GiB = 1024 ** 3
    MiB = 1024 ** 2
    if memlock >= GiB:
        return {"verdict": "ok",
                "reason": (f"MEMLOCK soft limit = {memlock // MiB} MiB "
                           "— enough headroom for mlock() of model weights."),
                "recommendation": ""}
    if memlock < 64 * MiB and vm_lck > 0:
        return {"verdict": "severely_low",
                "reason": (f"MEMLOCK soft limit = {memlock // 1024} KiB "
                           f"BUT VmLck = {vm_lck // MiB} MiB. mlock() is "
                           "failing silently — weights are paging out."),
                "recommendation": ("Drop-In recipe : raise LimitMEMLOCK="
                                    "infinity (see card body)")}
    return {"verdict": "low_limit",
            "reason": (f"MEMLOCK soft limit = {memlock // MiB} MiB. "
                       "If you start using --mlock with a model "
                       "larger than this, weights will silently swap."),
            "recommendation": ("Add LimitMEMLOCK=infinity to your "
                                "systemd unit Drop-In.")}


def systemd_dropin_recipe(comm: str) -> str:
    """Return a paste-ready Drop-In for the named daemon."""
    unit = (comm if comm and "/" not in comm and ".." not in comm
            else "your-service") + ".service"
    return (f"# Create /etc/systemd/system/{unit}.d/memlock.conf\n"
            f"[Service]\n"
            f"LimitMEMLOCK=infinity\n"
            f"# Apply : sudo systemctl daemon-reload && "
            f"sudo systemctl restart {unit}")


def status(cfg=None) -> dict:
    """Aggregate snapshot."""
    procs = scan_llm_procs()
    if not procs:
        return {"ok": True,
                "process_count": 0,
                "processes": [],
                "worst_verdict": "no_llm_procs",
                "summary": "No LLM runtime processes detected."}
    out: list = []
    rank = {"ok": 0, "low_limit": 1, "unknown": 1, "severely_low": 2}
    worst = "ok"
    for p in procs:
        verdict = classify(p)
        if rank.get(verdict["verdict"], 0) > rank.get(worst, 0):
            worst = verdict["verdict"]
        out.append({
            **p,
            "verdict": verdict,
            "recipe": (systemd_dropin_recipe(p["comm"])
                        if verdict["verdict"] in ("low_limit", "severely_low")
                        else ""),
        })
    return {"ok": True,
            "process_count": len(out),
            "processes": out,
            "worst_verdict": worst}
