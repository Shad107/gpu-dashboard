"""Module proc_wchan — wchan + stack inference-stuck debugger (R&D #32.3).

When an inference daemon's TTFT spikes from 200 ms to 5 s with no
obvious cause, the question "where is the process *right now*?"
usually answers it. The Linux `/proc/<pid>/wchan` file exposes the
kernel symbol the task is blocked on (e.g. `folio_wait_bit_common`,
`io_schedule`, `memory_reclaim`, `futex_wait_queue`), and the process
state from /proc/<pid>/status (R/S/D/Z) tells you whether the block
is interruptible.

This module reads wchan + state for every LLM daemon (ollama,
llama-server, vllm, ...), classifies the combination, and ties
hot-path symbols back to the shipped causal-chain modules:

  running           state=R                        ok
  idle              state=S, wchan=0               ok
  normal_wait       state=S, wchan ∈ {futex_*,
                    poll_*, do_select, sleep_*}    ok
  io_bound          state=D, wchan ∈ {io_schedule,
                    blk_*, bio_*}                  → #30.3 + #29.8
  page_cache_wait   state=D, wchan ∈ {folio_*,
                    wait_on_page_*}                → mmap thrash
  mem_pressure      state=D, wchan ∈ {memory_*,
                    shrink_*, kswapd_*}            → #32.4 + #29.8
  blocked           state=D, wchan unknown         generic D state
  zombie            state=Z                        defunct
  unknown           cannot read /proc/<pid>/status

/proc/<pid>/stack is read when accessible (typically requires
CAP_SYS_PTRACE → root); when denied we report `stack: null` rather
than throwing.

stdlib only.
"""
from __future__ import annotations

import os
import re
from typing import Optional


NAME = "proc_wchan"


_PROC = "/proc"


# Same heuristics as rlimit_audit / oom_priority / proc_smaps
LLM_COMM_PATTERNS = (
    "ollama", "llama-server", "llama_server", "llama.cpp", "llamacpp",
    "vllm", "sglang", "exllamav2", "exllama", "comfyui",
)
LLM_CMDLINE_HINTS = (
    "llama_cpp", "vllm.entrypoints", "ollama", "exllama",
    "text-generation-webui", "comfyui",
)


_STATE_NAMES = {
    "R": "running",
    "S": "sleeping",
    "D": "disk-sleep",
    "Z": "zombie",
    "T": "stopped",
    "t": "tracing-stop",
    "I": "idle",
    "X": "dead",
}


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


def read_wchan(pid: int, proc_root: str = _PROC) -> Optional[str]:
    t = _read(os.path.join(proc_root, str(pid), "wchan"))
    if t is None:
        return None
    s = t.strip()
    # Kernel writes literal "0" or empty when no wait channel.
    if not s or s == "0":
        return None
    return s


_STATE_RE = re.compile(r"^State:\s+([A-Za-z])", re.MULTILINE)


def read_state(pid: int, proc_root: str = _PROC) -> Optional[str]:
    t = _read(os.path.join(proc_root, str(pid), "status"))
    if t is None:
        return None
    m = _STATE_RE.search(t)
    return m.group(1) if m else None


def read_stack(pid: int, proc_root: str = _PROC) -> Optional[list]:
    """Return list of stack lines, or None on permission denied / absent."""
    p = os.path.join(proc_root, str(pid), "stack")
    if not os.path.exists(p):
        return None
    try:
        with open(p) as f:
            lines = [ln.strip() for ln in f.readlines() if ln.strip()]
    except OSError:
        return None
    return lines


# wchan symbol classification
_IO_WCHANS = (
    "io_schedule", "blk_", "bio_", "submit_bio", "blk_mq_",
    "blk_io_wait",
)

_PAGECACHE_WCHANS = (
    "folio_wait_bit_common", "wait_on_page_bit", "wait_on_page_writeback",
    "folio_wait_writeback", "filemap_fault",
)

_MEM_WCHANS = (
    "memory_reclaim", "shrink_node", "shrink_inactive", "shrink_lruvec",
    "shrink_node_memcgs", "kswapd", "out_of_memory", "__alloc_pages",
    "direct_reclaim",
)

_NORMAL_WAIT_WCHANS = (
    "futex_wait", "futex_wait_queue", "futex_wait_setup",
    "poll_schedule_timeout", "do_select", "ep_poll", "sys_poll",
    "skb_wait_for_more_packets", "epoll_wait", "do_epoll_wait",
    "sigtimedwait", "sigsuspend", "schedule_timeout",
    "do_wait", "do_nanosleep", "hrtimer_nanosleep",
    "pipe_wait", "wait_for_completion",
)


def _matches_any(wchan: str, patterns: tuple) -> bool:
    low = wchan.lower()
    return any(low.startswith(p) or p in low for p in patterns)


_RANK = {
    "running": 0,
    "idle": 0,
    "normal_wait": 0,
    "unknown": 1,
    "zombie": 2,
    "blocked": 3,
    "io_bound": 4,
    "page_cache_wait": 4,
    "mem_pressure": 5,
}


def classify(state: Optional[str], wchan: Optional[str]) -> dict:
    if state is None:
        return {"verdict": "unknown",
                "reason": "Cannot read /proc/<pid>/status (state).",
                "recommendation": ""}
    if state == "Z":
        return {"verdict": "zombie",
                "reason": "Process is a zombie (terminated, awaiting reap).",
                "recommendation": "# Restart the parent service unit."}
    if state == "R":
        return {"verdict": "running",
                "reason": "Currently scheduled on a CPU.",
                "recommendation": ""}
    if state == "S":
        if wchan is None:
            return {"verdict": "idle",
                    "reason": "Interruptible sleep with no specific wait channel.",
                    "recommendation": ""}
        if _matches_any(wchan, _NORMAL_WAIT_WCHANS):
            return {"verdict": "normal_wait",
                    "reason": (f"Sleeping in `{wchan}` — typical "
                               f"futex / poll / epoll idle."),
                    "recommendation": ""}
        # S state with an unusual wchan — still benign in most cases
        return {"verdict": "normal_wait",
                "reason": f"Sleeping in `{wchan}`.",
                "recommendation": ""}
    if state == "D":
        if wchan and _matches_any(wchan, _MEM_WCHANS):
            return {"verdict": "mem_pressure",
                    "reason": (f"Uninterruptible sleep in memory-reclaim "
                               f"path `{wchan}` — the kernel is freeing "
                               f"memory while your inference thread "
                               f"waits."),
                    "recommendation": (
                        "# Memory pressure during reclaim. Check the chain:\n"
                        "# 1. #32.4 vm_sysctl_audit  vm.swappiness ≤ 10\n"
                        "# 2. #29.8 rlimit_audit     LimitMEMLOCK on unit\n"
                        "# 3. #31.2 proc_smaps        Swap field on daemon\n"
                        "# Quick test: does `free -h` show low available?"
                    )}
        if wchan and _matches_any(wchan, _PAGECACHE_WCHANS):
            return {"verdict": "page_cache_wait",
                    "reason": (f"Uninterruptible sleep in `{wchan}` — "
                               f"waiting on the page cache (your GGUF "
                               f"mmap is being reread or evicted)."),
                    "recommendation": (
                        "# Page-cache thrash. Check:\n"
                        "# 1. #29.8 rlimit_audit — add --mlock + "
                        "LimitMEMLOCK=infinity\n"
                        "# 2. #30.3 nvme_iosched — scheduler=none + "
                        "read_ahead_kb=4096\n"
                        "# 3. #31.2 proc_smaps — confirm Pss_File >= "
                        "model size\n"
                    )}
        if wchan and _matches_any(wchan, _IO_WCHANS):
            return {"verdict": "io_bound",
                    "reason": (f"Uninterruptible sleep in IO path "
                               f"`{wchan}`."),
                    "recommendation": (
                        "# IO-bound. Check:\n"
                        "# 1. #30.3 nvme_iosched — scheduler=none, "
                        "increase read_ahead_kb\n"
                        "# 2. Run `iotop -o` to find the offending IO\n"
                    )}
        return {"verdict": "blocked",
                "reason": (f"Uninterruptible sleep in `{wchan or '<no wchan>'}`. "
                           f"Could be NFS, NVMe stall, or a kernel lock."),
                "recommendation": (
                    "# Generic D state. Investigate:\n"
                    "cat /proc/<pid>/stack   # need root\n"
                    "dmesg | tail -50         # look for NVMe / RAID errors"
                )}
    # T, t, I, X, etc.
    return {"verdict": "normal_wait",
            "reason": f"State `{state}` ({_STATE_NAMES.get(state, 'unknown')}).",
            "recommendation": ""}


def scan_llm_procs(proc_root: str = _PROC) -> list:
    out: list = []
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
            "state": read_state(pid, proc_root),
            "wchan": read_wchan(pid, proc_root),
            "stack": read_stack(pid, proc_root),
        })
    return out


def status(cfg=None) -> dict:
    procs = scan_llm_procs(_PROC)
    if not procs:
        return {"ok": True, "process_count": 0, "processes": [],
                "worst_verdict": "no_llm_procs"}
    enriched: list = []
    worst = "running"
    for p in procs:
        v = classify(p["state"], p["wchan"])
        if _RANK.get(v["verdict"], 0) > _RANK.get(worst, 0):
            worst = v["verdict"]
        enriched.append({**p, "verdict": v})
    return {"ok": True, "process_count": len(enriched),
            "processes": enriched, "worst_verdict": worst}
