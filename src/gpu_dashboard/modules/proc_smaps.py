"""Module proc_smaps — smaps_rollup residence breakdown (R&D #31.2).

`/proc/<pid>/smaps_rollup` is a 1-page summary of /proc/<pid>/maps,
exposing the same fields as /proc/<pid>/status (Rss, Pss, Swap) plus
the breakdown the dashboard actually needs:

  Pss_Anon   anonymous memory (KV cache, runtime allocs, malloc heap)
  Pss_File   file-backed memory (mmap'd GGUF, shared libs)
  Pss_Shmem  shared/tmpfs memory
  Swap       paged out — bad for inference
  Anonymous  total anon (regardless of PSS weighting)

For an LLM daemon, the *split* between Pss_File and Pss_Anon tells
you what the user actually has loaded:

  GGUF mmap loaded into page cache  → Pss_File grows toward model size
  KV cache + runtime state          → Pss_Anon
  Page cache eviction               → Pss_File shrinks toward zero
                                       while Rss stays high (kernel
                                       must reread on every inference)

Verdicts:

  ok            healthy residence breakdown
  mmap_evicted  Pss_File << model size → GGUF kicked out of page
                cache, kernel rereading from NVMe per inference
  swapping      Swap > a threshold → inference daemon is paging out
                (this is the failure mode #29.8 rlimit auditor
                catches at the cause; this catches it at the symptom)
  unreadable    smaps_rollup missing / empty (root-owned daemon for
                non-root caller, or kernel without CONFIG_PROC_PAGE_MONITOR)

stdlib only.
"""
from __future__ import annotations

import os
import re
from typing import Optional


NAME = "proc_smaps"


_PROC = "/proc"


# Same heuristics as rlimit_audit / oom_priority
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
    low = comm.lower()
    for pat in LLM_COMM_PATTERNS:
        if pat in low:
            return True
    if low.startswith("python") or low.startswith("uvicorn"):
        for h in LLM_CMDLINE_HINTS:
            if h in cmdline:
                return True
    return False


_FIELD_MAP = {
    "rss": "rss_kb",
    "pss": "pss_kb",
    "pss_anon": "pss_anon_kb",
    "pss_file": "pss_file_kb",
    "pss_shmem": "pss_shmem_kb",
    "anonymous": "anonymous_kb",
    "swap": "swap_kb",
    "swappss": "swap_pss_kb",
    "shared_clean": "shared_clean_kb",
    "shared_dirty": "shared_dirty_kb",
    "private_clean": "private_clean_kb",
    "private_dirty": "private_dirty_kb",
    "locked": "locked_kb",
    "referenced": "referenced_kb",
}


_FIELD_RE = re.compile(r"^([A-Za-z_]+):\s+(\d+)\s+kB", re.MULTILINE)


def parse_rollup(text: str) -> dict:
    if not text:
        return {}
    out: dict = {}
    for m in _FIELD_RE.finditer(text):
        k = m.group(1).strip().lower()
        target = _FIELD_MAP.get(k)
        if target:
            out[target] = int(m.group(2))
    return out


# --- classify -------------------------------------------------------

_SWAP_THRESHOLD_KB = 1_000_000          # 1 GiB swap → real pressure
_MMAP_FLOOR_KB = 500_000                # < 500 MiB file-backed = evicted
_MMAP_RATIO_FLOOR = 0.05                # < 5 % of pss is file → evicted


def classify(rollup: dict) -> dict:
    if not rollup or "pss_kb" not in rollup:
        return {"verdict": "unreadable",
                "reason": ("smaps_rollup absent, empty, or unreadable "
                           "(root-owned daemon for non-root caller, or "
                           "kernel without CONFIG_PROC_PAGE_MONITOR)."),
                "recommendation": ""}
    swap = rollup.get("swap_kb", 0)
    if swap >= _SWAP_THRESHOLD_KB:
        return {
            "verdict": "swapping",
            "reason": (f"{swap // 1024} MiB of this process is in swap. "
                       f"Inference will stall on swap-in for every "
                       f"page touched."),
            "recommendation": (
                "# Verify --mlock (or LimitMEMLOCK=infinity) is in effect ;\n"
                "# see #29.8 rlimit auditor. Then check host swap pressure:\n"
                "swapon --show\n"
                "vmstat 1 5\n"
                "# Disable swap for an air-gapped LLM box:\n"
                "sudo swapoff -a"
            ),
        }
    pss = rollup.get("pss_kb", 0)
    pss_file = rollup.get("pss_file_kb", 0)
    pss_anon = rollup.get("pss_anon_kb", 0)
    # Heuristic: if anon dominates almost completely AND file share is
    # tiny in absolute terms, the GGUF mmap was likely evicted.
    if (pss > 5_000_000  # process > 5 GiB (not a small util)
            and pss_file < _MMAP_FLOOR_KB
            and (pss_file / pss if pss else 0) < _MMAP_RATIO_FLOOR
            and pss_anon > pss_file * 5):
        return {
            "verdict": "mmap_evicted",
            "reason": (f"Only {pss_file // 1024} MiB of {pss // 1024} MiB "
                       f"PSS is file-backed — the GGUF mmap likely got "
                       f"evicted from the page cache. The kernel will "
                       f"reread it from NVMe on every inference."),
            "recommendation": (
                "# Bump LimitMEMLOCK and add --mlock (#29.8 rlimit auditor),\n"
                "# or simply add more host RAM. To dump current mmap state:\n"
                "cat /proc/$PID/smaps | grep -A1 '.gguf' | head"
            ),
        }
    return {
        "verdict": "ok",
        "reason": (f"PSS={pss // 1024} MiB split as "
                   f"anon={pss_anon // 1024} MiB + "
                   f"file={pss_file // 1024} MiB + "
                   f"swap={swap // 1024} MiB — healthy LLM residence."),
        "recommendation": "",
    }


_RANK = {
    "ok": 0,
    "unreadable": 1,
    "mmap_evicted": 2,
    "swapping": 3,
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
        rollup_path = os.path.join(proc_root, str(pid), "smaps_rollup")
        rollup_text = _read(rollup_path) or ""
        rollup = parse_rollup(rollup_text)
        out.append({
            "pid": pid,
            "comm": comm,
            "cmdline_short": cmdline[:140],
            "rollup": rollup,
        })
    return out


def status(cfg=None) -> dict:
    procs = scan_llm_procs(_PROC)
    if not procs:
        return {"ok": True, "process_count": 0, "processes": [],
                "worst_verdict": "no_llm_procs",
                "total_rss_bytes": 0}
    enriched: list = []
    worst = "ok"
    total_rss = 0
    for p in procs:
        roll = p["rollup"]
        v = classify(roll)
        rss = roll.get("rss_kb", 0)
        total_rss += rss
        enriched.append({
            "pid": p["pid"],
            "comm": p["comm"],
            "cmdline_short": p["cmdline_short"],
            "rss_bytes": rss * 1024,
            "pss_bytes": roll.get("pss_kb", 0) * 1024,
            "pss_anon_bytes": roll.get("pss_anon_kb", 0) * 1024,
            "pss_file_bytes": roll.get("pss_file_kb", 0) * 1024,
            "pss_shmem_bytes": roll.get("pss_shmem_kb", 0) * 1024,
            "anonymous_bytes": roll.get("anonymous_kb", 0) * 1024,
            "swap_bytes": roll.get("swap_kb", 0) * 1024,
            "verdict": v,
        })
        if _RANK.get(v["verdict"], 0) > _RANK.get(worst, 0):
            worst = v["verdict"]
    return {"ok": True, "process_count": len(enriched),
            "processes": enriched, "worst_verdict": worst,
            "total_rss_bytes": total_rss * 1024}
