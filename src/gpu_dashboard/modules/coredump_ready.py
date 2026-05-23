"""Module coredump_ready — coredump-readiness auditor (R&D #39.3).

When llama-server / ollama crashes (SIGSEGV from a CUDA driver bug, a
NULL deref in a Python wrapper), the user wants a usable core file
on disk so they can `gdb -c core /path/to/binary` and report
something useful upstream. The actual mechanics:

  /proc/sys/kernel/core_pattern        where the core goes:
                                        "core" → CWD/core (collision!)
                                        "/path/with-%p-%e"  → absolute
                                        "|/path/to/handler"  → kernel
                                          pipes the dump to a process
                                          (systemd-coredump, apport)
                                        "|/bin/false" → disabled
  /proc/sys/kernel/core_uses_pid       1 = append pid suffix
  /proc/<pid>/coredump_filter          hex bitmask of memory types
                                        to include in the core:
                                          0x01 anon private (KV cache)
                                          0x02 anon shared
                                          0x04 file private
                                          0x08 file shared
                                          0x10 ELF headers
                                          0x20 huge private
                                          0x40 huge shared

Default 0x33 = anon-private+anon-shared+elf+huge-private. For an
inference daemon that's "good enough" — anon covers the runtime
heap and KV cache.

Verdicts:
  core_disabled       pattern is `|/bin/false` or empty
  ok_pipe_handler     pattern is `|<path>` to systemd-coredump etc.
  ok_file_based       pattern is an absolute path with %p / %e
                      (collision-free, usable filename)
  relative_pattern    pattern is literally "core" or relative ;
                      dumps land in CWD with name collisions
  filter_too_low      ≥1 LLM daemon has a filter < 0x33 — will
                      miss the KV-cache or ELF headers
  unknown             /proc/sys/kernel/core_pattern unreadable

stdlib only.
"""
from __future__ import annotations

import os
import re
from typing import Optional


NAME = "coredump_ready"


_PROC = "/proc"


LLM_COMM_PATTERNS = (
    "ollama", "llama-server", "llama_server", "llama.cpp", "llamacpp",
    "vllm", "sglang", "exllamav2", "exllama", "comfyui",
)
LLM_CMDLINE_HINTS = (
    "llama_cpp", "vllm.entrypoints", "ollama", "exllama",
    "text-generation-webui", "comfyui",
)


def parse_coredump_filter(s: Optional[str]) -> Optional[int]:
    if not s:
        return None
    v = s.strip()
    if v.lower().startswith("0x"):
        v = v[2:]
    try:
        return int(v, 16)
    except ValueError:
        return None


_FILTER_BITS = [
    (0x01, "anon_private", "anonymous private (KV cache, runtime heap)"),
    (0x02, "anon_shared", "anonymous shared (shared memory)"),
    (0x04, "file_private", "file-backed private (mapped libraries)"),
    (0x08, "file_shared", "file-backed shared"),
    (0x10, "elf_headers", "ELF headers (needed for symbol lookup)"),
    (0x20, "huge_private", "huge private (2 MiB anon pages)"),
    (0x40, "huge_shared", "huge shared"),
    (0x80, "dax_private", "DAX private"),
    (0x100, "dax_shared", "DAX shared"),
]


def describe_filter(value: int) -> list:
    out: list = []
    for mask, key, desc in _FILTER_BITS:
        if value & mask:
            out.append({"key": key, "mask": mask, "description": desc})
    return out


def analyze_core_pattern(pattern: str) -> dict:
    if not pattern:
        return {"kind": "unknown", "target": "", "has_pid": False,
                "has_exe": False}
    p = pattern.strip()
    if p.startswith("|"):
        target = p[1:].split()[0] if len(p) > 1 else ""
        kind = "disabled" if target.endswith("/false") or target == "" else "pipe_handler"
        return {"kind": kind, "target": target,
                "has_pid": "%p" in p, "has_exe": "%e" in p}
    has_pid = "%p" in p
    has_exe = "%e" in p
    if p.startswith("/"):
        return {"kind": "file_based", "target": p,
                "has_pid": has_pid, "has_exe": has_exe}
    return {"kind": "relative_only", "target": p,
            "has_pid": has_pid, "has_exe": has_exe}


_DEFAULT_FILTER_MIN = 0x11   # anon_private + elf_headers minimum


_RECIPE_PATTERN = (
    "# Set an absolute core-pattern with %p (pid) + %e (exe name):\n"
    "echo '/var/crash/core.%e.%p.%t' | sudo tee /proc/sys/kernel/core_pattern\n"
    "# Persist via sysctl.d:\n"
    "echo 'kernel.core_pattern=/var/crash/core.%e.%p.%t' | \\\n"
    "  sudo tee /etc/sysctl.d/99-core-pattern.conf\n"
    "sudo sysctl --system\n"
    "# Also ensure ulimit -c is unlimited for the daemon:\n"
    "# add LimitCORE=infinity to the systemd unit."
)

_RECIPE_DISABLED = (
    "# Core dumps are disabled via core_pattern=|/bin/false. To enable:\n"
    "# Install systemd-coredump (Debian/Ubuntu):\n"
    "sudo apt install systemd-coredump\n"
    "# Or set an explicit absolute pattern (see relative_pattern verdict)."
)

_RECIPE_FILTER = (
    "# Some LLM daemon has a low coredump_filter (< 0x33). Bump it:\n"
    "echo 0x33 | sudo tee /proc/<pid>/coredump_filter\n"
    "# Persistent via systemd: CoredumpFilter=0x33 in the unit.\n"
    "# Or kernel-wide via /proc/sys/kernel/...\n"
)


_RANK = {"ok_pipe_handler": 0, "ok_file_based": 0,
         "relative_pattern": 2, "filter_too_low": 3,
         "core_disabled": 4, "unknown": 1}


def classify(pattern_info: dict, procs: list) -> dict:
    kind = pattern_info.get("kind")
    if kind == "disabled":
        return {"verdict": "core_disabled",
                "reason": ("Kernel core_pattern is `|/bin/false` "
                           "(or empty pipe handler) — core dumps "
                           "are explicitly discarded."),
                "recommendation": _RECIPE_DISABLED}
    if kind == "unknown":
        return {"verdict": "unknown",
                "reason": "core_pattern unreadable.",
                "recommendation": ""}
    # Pattern is acceptable ; now check per-proc filters
    too_low = [p for p in procs
                if p.get("filter") is not None
                and p["filter"] < _DEFAULT_FILTER_MIN]
    if too_low:
        names = ", ".join(f"{p['comm']}(pid {p['pid']}, filter=0x{p['filter']:x})"
                            for p in too_low)
        return {"verdict": "filter_too_low",
                "reason": (f"{len(too_low)} LLM daemon(s) have "
                           f"coredump_filter < 0x33 — core dumps "
                           f"will miss anon memory or ELF headers. "
                           f"{names}"),
                "recommendation": _RECIPE_FILTER}
    if kind == "relative_only":
        return {"verdict": "relative_pattern",
                "reason": (f"core_pattern is `{pattern_info.get('target')}` — "
                           f"relative path means dumps land in the "
                           f"daemon's CWD. With multiple LLM daemons "
                           f"the filenames collide ; harder to "
                           f"find post-crash."),
                "recommendation": _RECIPE_PATTERN}
    if kind == "pipe_handler":
        return {"verdict": "ok_pipe_handler",
                "reason": (f"Kernel pipes core dumps to "
                           f"{pattern_info.get('target')} — typically "
                           f"systemd-coredump or apport, both keep "
                           f"the dumps in a known location with "
                           f"deduplication."),
                "recommendation": ""}
    # file_based
    if pattern_info.get("has_pid"):
        return {"verdict": "ok_file_based",
                "reason": (f"core_pattern is absolute "
                           f"`{pattern_info.get('target')}` with %p — "
                           f"collision-free filenames."),
                "recommendation": ""}
    return {"verdict": "relative_pattern",
            "reason": (f"core_pattern is absolute but lacks %p — "
                       f"multiple crashes overwrite each other."),
            "recommendation": _RECIPE_PATTERN}


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
            return f.read().replace(b"\x00", b" ").decode(
                "utf-8", errors="replace")
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
        f = parse_coredump_filter(
            (_read(os.path.join(proc_root, str(pid), "coredump_filter"))
             or ""))
        out.append({
            "pid": pid, "comm": comm,
            "cmdline_short": cmdline[:140],
            "filter": f,
            "filter_value": f,
            "filter_bits": describe_filter(f or 0),
        })
    return out


def status(cfg=None) -> dict:
    pattern = (_read(os.path.join(_PROC, "sys", "kernel", "core_pattern"))
                or "")
    uses_pid_text = _read(os.path.join(_PROC, "sys", "kernel",
                                          "core_uses_pid")) or ""
    uses_pid = uses_pid_text.strip() == "1"
    pattern_info = analyze_core_pattern(pattern.strip())
    procs = scan_llm_procs(_PROC)
    verdict = classify(pattern_info, procs)
    return {
        "ok": True,
        "core_pattern": pattern.strip(),
        "core_uses_pid": uses_pid,
        "pattern_info": pattern_info,
        "process_count": len(procs),
        "processes": procs,
        "verdict": verdict,
    }
