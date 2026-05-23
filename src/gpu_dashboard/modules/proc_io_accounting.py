"""Module proc_io_accounting — per-daemon IO accounting (R&D #33.2).

`/proc/<pid>/io` (kernel CONFIG_TASK_IO_ACCOUNTING=y, on by default
since 2.6.20) exposes cumulative I/O totals per task:

  rchar               total chars read by read() syscalls
  wchar               total chars written by write() syscalls
  syscr / syscw       syscall counts
  read_bytes          bytes pulled from the block device (page-cache
                      misses + direct IO)
  write_bytes         bytes written to the block device
  cancelled_write_bytes

For an LLM daemon, `read_bytes` is the diagnostic: if it's much
larger than the process RSS, the GGUF mmap is being reread from
disk on every inference (page cache eviction). The user's
foot-gun chain looks like:

  vm.swappiness=60  (#32.4)          swaps anon pages out
  → that evicts hot mmap pages       (page cache invalidated)
  → kernel rereads GGUF from disk    (read_bytes >> RSS)
  → inference TTFT spikes from 200 ms to 5 s

Verdicts:
  ok              read_bytes < 2× RSS, write_bytes < 1 GiB
  reread_thrash   read_bytes >= 2× RSS — GGUF reread from disk
  heavy_write     write_bytes >= 10 GiB — unusual for inference
  unreadable      /proc/<pid>/io empty or absent (root-owned proc)

stdlib only.
"""
from __future__ import annotations

import os
import re
from typing import Optional


NAME = "proc_io_accounting"


_PROC = "/proc"


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


_IO_FIELDS = (
    "rchar", "wchar", "syscr", "syscw",
    "read_bytes", "write_bytes", "cancelled_write_bytes",
)


_IO_LINE_RE = re.compile(r"^([a-z_]+):\s+(\d+)\s*$", re.MULTILINE)


def parse_io(text: str) -> dict:
    if not text:
        return {}
    out: dict = {}
    for m in _IO_LINE_RE.finditer(text):
        k = m.group(1)
        if k in _IO_FIELDS:
            out[k] = int(m.group(2))
    return out


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


_THRASH_RATIO = 2.0              # read_bytes >= 2.0 × RSS
_THRASH_ABS_THRESHOLD = 200 * 1024 ** 3   # 200 GiB absolute fallback
_HEAVY_WRITE_THRESHOLD = 10 * 1024 ** 3   # 10 GiB written


_RANK = {
    "ok": 0,
    "unreadable": 1,
    "heavy_write": 2,
    "reread_thrash": 3,
}


def classify(io: dict, rss_bytes: Optional[int]) -> dict:
    if not io or "read_bytes" not in io:
        return {"verdict": "unreadable",
                "reason": ("/proc/<pid>/io empty or absent (root-owned "
                           "daemon for non-root caller)."),
                "recommendation": ""}
    rb = io.get("read_bytes", 0)
    wb = io.get("write_bytes", 0)
    if rss_bytes and rss_bytes > 0:
        ratio = rb / rss_bytes
        if ratio >= _THRASH_RATIO and rb >= 1 * 1024 ** 3:
            return {"verdict": "reread_thrash",
                    "reason": (f"read_bytes={rb / 1024**3:.1f} GiB is "
                               f"{ratio:.1f}× RSS "
                               f"({rss_bytes / 1024**3:.1f} GiB) — your "
                               f"GGUF mmap keeps getting evicted, kernel "
                               f"rereads from NVMe on every inference."),
                    "recommendation": _causal_chain_recipe()}
    elif rb >= _THRASH_ABS_THRESHOLD:
        return {"verdict": "reread_thrash",
                "reason": (f"read_bytes={rb / 1024**3:.0f} GiB exceeds "
                           f"the absolute threshold ({_THRASH_ABS_THRESHOLD / 1024**3:.0f} "
                           f"GiB) and no RSS reference is available."),
                "recommendation": _causal_chain_recipe()}
    if wb >= _HEAVY_WRITE_THRESHOLD:
        return {"verdict": "heavy_write",
                "reason": (f"write_bytes={wb / 1024**3:.1f} GiB — unusual "
                           f"for an inference daemon. Check whether the "
                           f"daemon is logging to disk or persisting "
                           f"cache."),
                "recommendation": ""}
    return {"verdict": "ok",
            "reason": (f"read_bytes={rb / 1024**3:.1f} GiB, write_bytes="
                       f"{wb / 1024**3:.1f} GiB — healthy IO profile."),
            "recommendation": ""}


def _causal_chain_recipe() -> str:
    return (
        "# Page-cache thrash — the GGUF mmap is being reread from disk.\n"
        "# Causal chain (in priority order):\n"
        "# 1. #32.4 vm_sysctl_audit  — vm.swappiness ≤ 10 stops anon\n"
        "#                            swap-out from evicting your mmap\n"
        "# 2. #29.8 rlimit_audit     — LimitMEMLOCK=infinity + --mlock\n"
        "# 3. #30.3 nvme_iosched     — scheduler=none + read_ahead_kb=4096\n"
        "# 4. #31.2 proc_smaps       — confirm Pss_File ≈ model size\n"
        "# 5. Add more host RAM if the cause→symptom chain is clean."
    )


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
        io_text = _read(os.path.join(proc_root, str(pid), "io")) or ""
        out.append({
            "pid": pid,
            "comm": comm,
            "cmdline_short": cmdline[:140],
            "io": parse_io(io_text),
            "vm_rss_bytes": read_vm_rss_bytes(pid, proc_root),
        })
    return out


def status(cfg=None) -> dict:
    procs = scan_llm_procs(_PROC)
    if not procs:
        return {"ok": True, "process_count": 0, "processes": [],
                "worst_verdict": "no_llm_procs",
                "total_read_bytes": 0, "total_write_bytes": 0}
    enriched: list = []
    worst = "ok"
    total_r = total_w = 0
    for p in procs:
        io = p["io"]
        v = classify(io, p["vm_rss_bytes"])
        if _RANK.get(v["verdict"], 0) > _RANK.get(worst, 0):
            worst = v["verdict"]
        rb = io.get("read_bytes", 0)
        wb = io.get("write_bytes", 0)
        total_r += rb
        total_w += wb
        enriched.append({
            "pid": p["pid"],
            "comm": p["comm"],
            "cmdline_short": p["cmdline_short"],
            "read_bytes": rb,
            "write_bytes": wb,
            "rchar": io.get("rchar"),
            "wchar": io.get("wchar"),
            "syscr": io.get("syscr"),
            "syscw": io.get("syscw"),
            "vm_rss_bytes": p["vm_rss_bytes"],
            "verdict": v,
        })
    return {"ok": True, "process_count": len(enriched),
            "processes": enriched, "worst_verdict": worst,
            "total_read_bytes": total_r,
            "total_write_bytes": total_w}
