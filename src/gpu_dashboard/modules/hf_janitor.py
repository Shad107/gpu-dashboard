"""Module hf_janitor — surface large stale model files (R&D #9.4).

Most LLM rig users have 50-500 GB of GGUF / safetensors files they
forgot about. This module walks common model dirs and sorts entries
by 'cold' (largest × oldest atime), letting the user prune confidently.

Discovered dirs (in priority order, all optional) :
  ~/.cache/huggingface/                — HF hub cache + downloaded snapshots
  /home/olivier/models/                — user-configurable via MODELS_DIRS env
  ~/.ollama/models/                    — Ollama
  ~/.cache/llama.cpp/                  — llama.cpp cache
  ~/.cache/comfy/                      — ComfyUI

Output is read-only — never deletes. User does the rm themselves with
a generated CSV they can pipe to xargs.

stdlib only.
"""
from __future__ import annotations

import os
import time
from typing import Iterable, Optional


NAME = "hf_janitor"

# Where to look. User-overridable via the MODELS_DIRS config (comma-separated).
_DEFAULT_DIRS = [
    "~/.cache/huggingface",
    "~/.ollama/models",
    "~/.cache/llama.cpp",
    "~/.cache/comfy",
]

# File extensions we consider 'model data' (vs metadata)
_MODEL_EXTS = (".gguf", ".safetensors", ".bin", ".pt", ".pth", ".onnx",
               ".q4_0", ".q4_k_m", ".q5_k_m", ".q8_0", ".f16")

_MIN_SIZE_MIB = 50  # ignore tiny files


def expand_dirs(extra: Optional[Iterable[str]] = None) -> list:
    """Resolve all candidate dirs that actually exist."""
    candidates = list(_DEFAULT_DIRS)
    if extra:
        candidates.extend(extra)
    out: list = []
    for d in candidates:
        full = os.path.expanduser(d)
        if os.path.isdir(full):
            out.append(full)
    return out


def is_model_file(path: str, size_bytes: int) -> bool:
    """Heuristic : extension matches OR file is huge (>1 GiB)."""
    if size_bytes < _MIN_SIZE_MIB * 1024 * 1024:
        return False
    lower = path.lower()
    return lower.endswith(_MODEL_EXTS) or size_bytes >= 1024 * 1024 * 1024


def scan_dir(root: str, hot_pids: Optional[set] = None) -> list:
    """Walk `root`, return list of {path, size_mib, atime, mtime, age_days,
    is_hot, dir_top} for files matching model heuristic.

    hot_pids : set of PIDs that have files open (avoid suggesting deletion of
    actively-loaded models). We don't actually cross-check here — the caller
    does that via /proc/<pid>/maps.
    """
    out: list = []
    now = time.time()
    try:
        for dirpath, _, files in os.walk(root, followlinks=False):
            for name in files:
                fpath = os.path.join(dirpath, name)
                try:
                    st = os.stat(fpath, follow_symlinks=False)
                except OSError:
                    continue
                if not is_model_file(fpath, st.st_size):
                    continue
                age_s = now - st.st_atime
                # top-level dir within root (e.g. 'hub' under huggingface)
                rel = os.path.relpath(fpath, root)
                top = rel.split(os.sep, 1)[0] if os.sep in rel else rel
                out.append({
                    "path": fpath,
                    "size_mib": int(st.st_size / 1024 / 1024),
                    "atime": int(st.st_atime),
                    "mtime": int(st.st_mtime),
                    "age_days": int(age_s / 86400),
                    "dir_top": top,
                    "root": root,
                })
    except OSError:
        pass
    return out


def find_hot_paths() -> set:
    """Scan /proc/*/maps for memory-mapped files in our target dirs.
    Returns set of absolute paths currently mapped by any running process.
    Used to flag 'do not delete' candidates."""
    hot: set = set()
    import glob
    for proc_dir in glob.glob("/proc/[0-9]*"):
        maps_path = os.path.join(proc_dir, "maps")
        try:
            with open(maps_path) as f:
                for line in f:
                    # mmap lines end with the file path after the last space
                    parts = line.rstrip("\n").split(maxsplit=5)
                    if len(parts) >= 6:
                        path = parts[5]
                        if path.startswith("/") and is_model_file(path, _MIN_SIZE_MIB * 1024 * 1024):
                            hot.add(path)
        except (OSError, PermissionError):
            continue
    return hot


def cold_score(entry: dict) -> float:
    """Higher = colder (bigger + older). Used for sort.
    score = age_days × log10(size_mib + 1)"""
    import math
    return entry["age_days"] * math.log10(max(1, entry["size_mib"]) + 1)


def audit(extra_dirs: Optional[Iterable[str]] = None,
          limit: int = 50) -> dict:
    """Top-level audit. Returns sorted cold-list + hot-set + summary."""
    dirs = expand_dirs(extra_dirs)
    if not dirs:
        return {"ok": True, "available": False, "reason": "no model dirs found"}
    hot = find_hot_paths()
    all_files: list = []
    for root in dirs:
        all_files.extend(scan_dir(root))
    for f in all_files:
        f["is_hot"] = f["path"] in hot
    all_files.sort(key=cold_score, reverse=True)
    total_size_mib = sum(f["size_mib"] for f in all_files)
    cold_size_mib = sum(f["size_mib"] for f in all_files if not f["is_hot"])
    return {
        "ok": True,
        "available": True,
        "dirs_scanned": dirs,
        "files_total": len(all_files),
        "total_size_mib": total_size_mib,
        "cold_size_mib": cold_size_mib,
        "hot_count": sum(1 for f in all_files if f["is_hot"]),
        "top_cold": all_files[:limit],
    }
