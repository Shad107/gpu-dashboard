"""Module proc_maps_libs — shared-library version drift (R&D #38.3).

After a driver upgrade (`apt upgrade` bumping nvidia-driver-XYZ), the
new libcuda.so / libnvidia-*.so files replace the on-disk files, but
any inference daemon already mapping the old version keeps the
*old* file alive via mmap. Linux marks the dentry as `(deleted)` in
/proc/<pid>/maps but the inode + pages stay resident in the daemon.

Symptoms: subtle CUDA-runtime mismatches, "this binary was built
against X but you're loading Y", kernel-module ABI errors. The fix
is always the same — restart the daemon to pick up the new library.

This module reads /proc/<pid>/maps for every LLM daemon, extracts
.so mappings, flags `(deleted)` ones, and identifies NVIDIA-family
libraries (libcuda*, libcudart*, libcublas*, libcudnn*, libnvidia*)
for the recipe text.

Verdicts:
  clean            no (deleted) markers — all libs valid on disk
  deleted_libs     ≥1 .so file is (deleted) but still mapped —
                   restart the daemon
  unreadable       /proc/<pid>/maps absent / restricted (root-owned)
  no_llm_procs     no inference daemons detected
  unknown          empty maps file

stdlib only.
"""
from __future__ import annotations

import os
import re
from typing import Optional


NAME = "proc_maps_libs"


_PROC = "/proc"


LLM_COMM_PATTERNS = (
    "ollama", "llama-server", "llama_server", "llama.cpp", "llamacpp",
    "vllm", "sglang", "exllamav2", "exllama", "comfyui",
)
LLM_CMDLINE_HINTS = (
    "llama_cpp", "vllm.entrypoints", "ollama", "exllama",
    "text-generation-webui", "comfyui",
)


_MAPS_RE = re.compile(
    r"^[0-9a-f]+-[0-9a-f]+\s+\S+\s+[0-9a-f]+\s+\S+\s+\d+\s+(.+?)$"
)


def parse_maps_line(line: str) -> Optional[dict]:
    if not line:
        return None
    s = line.rstrip()
    m = _MAPS_RE.match(s)
    if not m:
        return None
    path = m.group(1).strip()
    if not path:
        return None
    # Skip anonymous (path is "0" sometimes, special entries in brackets)
    if path.startswith("[") and path.endswith("]"):
        return None
    deleted = False
    if path.endswith(" (deleted)"):
        path = path[:-len(" (deleted)")]
        deleted = True
    return {"path": path, "deleted": deleted}


_NVIDIA_LIB_PREFIXES = (
    "libcuda", "libcudart", "libcublas", "libcublasLt",
    "libcudnn", "libnvidia",
)


def is_nvidia_lib(basename: str) -> bool:
    return any(basename.startswith(p) for p in _NVIDIA_LIB_PREFIXES)


def extract_libs(maps_text: str) -> list:
    """Group by (basename, path, deleted) → one entry per unique lib."""
    if not maps_text:
        return []
    seen: dict = {}
    for line in maps_text.splitlines():
        rec = parse_maps_line(line)
        if not rec:
            continue
        path = rec["path"]
        # Only .so files
        if ".so" not in os.path.basename(path):
            continue
        basename = os.path.basename(path)
        key = (basename, path, rec["deleted"])
        if key in seen:
            continue
        seen[key] = {
            "basename": basename,
            "path": path,
            "deleted": rec["deleted"],
            "is_nvidia": is_nvidia_lib(basename),
        }
    return list(seen.values())


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


_RECIPE_RESTART = (
    "# Driver / libs were upgraded under a running daemon. Restart:\n"
    "sudo systemctl restart <unit>.service\n"
    "# (replace <unit> with ollama, llama-server, vllm, etc.)\n"
    "# After restart, re-check this card — (deleted) should clear."
)


_RANK = {
    "clean": 0, "no_llm_procs": 0, "unreadable": 1, "unknown": 1,
    "deleted_libs": 3,
}


def classify(cards: list) -> dict:
    if not cards:
        return {"verdict": "no_llm_procs",
                "reason": "No LLM runtime processes detected.",
                "recommendation": ""}
    # If anyone has deleted libs → worst
    deleted = [c for c in cards if c.get("deleted_libs")]
    if deleted:
        examples = []
        for c in deleted[:2]:
            examples.append(
                f"{c['comm']} (pid {c['pid']}): "
                f"{', '.join(c['deleted_libs'][:3])}"
            )
        return {"verdict": "deleted_libs",
                "reason": (f"{len(deleted)} daemon(s) have (deleted) "
                           f"shared library mmaps. Examples: "
                           f"{'; '.join(examples)}. Driver / .so files "
                           f"were upgraded on disk while the daemons "
                           f"kept their old version mapped."),
                "recommendation": _RECIPE_RESTART}
    # All readable + no deleted → clean
    any_readable = any(c.get("readable") for c in cards)
    if not any_readable:
        return {"verdict": "unreadable",
                "reason": ("No LLM daemon /proc/<pid>/maps was readable "
                           "— typical for root-owned daemons when the "
                           "dashboard runs as a non-root user."),
                "recommendation": ""}
    total_libs = sum(len(c.get("libs", [])) for c in cards)
    nvidia_libs = sum(1 for c in cards
                       for lib in c.get("libs", [])
                       if lib.get("is_nvidia"))
    return {"verdict": "clean",
            "reason": (f"{total_libs} shared lib(s) mapped across "
                       f"{len(cards)} daemon(s), {nvidia_libs} NVIDIA-"
                       f"family lib(s) — no (deleted) markers, all "
                       f"point at current on-disk files."),
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
        maps_text = _read(os.path.join(proc_root, str(pid), "maps"))
        if maps_text is None:
            out.append({
                "pid": pid, "comm": comm,
                "cmdline_short": cmdline[:140],
                "libs": [], "deleted_libs": [],
                "readable": False,
            })
            continue
        libs = extract_libs(maps_text)
        deleted_libs = [lib["basename"] for lib in libs if lib["deleted"]]
        out.append({
            "pid": pid, "comm": comm,
            "cmdline_short": cmdline[:140],
            "libs": libs,
            "deleted_libs": deleted_libs,
            "readable": True,
        })
    return out


def status(cfg=None) -> dict:
    cards = scan_llm_procs(_PROC)
    verdict = classify(cards)
    return {
        "ok": True,
        "process_count": len(cards),
        "processes": cards,
        "verdict": verdict,
        "worst_verdict": verdict["verdict"],
    }
