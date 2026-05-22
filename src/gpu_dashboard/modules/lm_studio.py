"""Module lm_studio — LM-Studio model inventory bridge (R&D #16.7).

LM-Studio is a popular desktop GUI for local LLMs. It maintains a model
catalog at ~/.lmstudio/models/ with the same GGUF files HF Hub uses,
which means duplicates can pile up when users have both LM-Studio AND
the HF cache populated.

This module :
  - Walks ~/.lmstudio/models/ (or LM_STUDIO_MODELS_DIR env override)
  - Reads each GGUF's header magic + basic metadata (no full parse)
  - Cross-references with HF cache files of identical size to flag
    candidates for dedup (full SHA verification deferred to R&D #15.3)
  - Reads ~/.lmstudio/settings.json for default model + ctx length

stdlib only.
"""
from __future__ import annotations

import os
import struct
from typing import Optional


NAME = "lm_studio"

_DEFAULT_MODELS_DIR = "~/.lmstudio/models"
_SETTINGS_PATH = "~/.lmstudio/settings.json"
_HF_CACHE_DIR = "~/.cache/huggingface"


def models_dir() -> str:
    env = os.environ.get("LM_STUDIO_MODELS_DIR")
    if env:
        return os.path.expanduser(env)
    return os.path.expanduser(_DEFAULT_MODELS_DIR)


def settings_path() -> str:
    return os.path.expanduser(_SETTINGS_PATH)


def parse_gguf_header(path: str) -> dict:
    """Read the GGUF magic + minimum metadata fields. Returns a dict :
      {is_gguf, version, tensor_count, metadata_kv_count}
    or {is_gguf: False, reason: '...'} on any error."""
    try:
        with open(path, "rb") as f:
            magic = f.read(4)
            if magic != b"GGUF":
                return {"is_gguf": False, "reason": "not a GGUF file"}
            # version + tensor_count + metadata_kv_count (all little-endian)
            version_bytes = f.read(4)
            tensors_bytes = f.read(8)
            kv_bytes = f.read(8)
            if len(version_bytes) < 4 or len(tensors_bytes) < 8 or len(kv_bytes) < 8:
                return {"is_gguf": False, "reason": "truncated header"}
            version = struct.unpack("<I", version_bytes)[0]
            tensor_count = struct.unpack("<Q", tensors_bytes)[0]
            kv_count = struct.unpack("<Q", kv_bytes)[0]
            return {
                "is_gguf": True,
                "version": version,
                "tensor_count": tensor_count,
                "metadata_kv_count": kv_count,
            }
    except (OSError, struct.error):
        return {"is_gguf": False, "reason": "read error"}


def _infer_quant_from_name(name: str) -> Optional[str]:
    """Best-effort quantization label from filename suffix."""
    upper = name.upper()
    for q in ("Q2_K_M", "Q2_K", "Q3_K_S", "Q3_K_M", "Q3_K_L",
              "Q4_0", "Q4_1", "Q4_K_S", "Q4_K_M", "Q4_K_XL",
              "Q5_0", "Q5_1", "Q5_K_S", "Q5_K_M",
              "Q6_K", "Q8_0", "F16", "BF16", "F32"):
        if q in upper:
            return q
    return None


def scan_models(root: Optional[str] = None) -> list:
    """Walk the LM-Studio models dir. Returns list of :
      {path, name, size_bytes, size_mib, atime, mtime, is_gguf,
       gguf_version, tensor_count, kv_count, quant?, dir_top}
    """
    base = root or models_dir()
    if not os.path.isdir(base):
        return []
    out: list = []
    for dirpath, _, files in os.walk(base, followlinks=False):
        for name in files:
            if not name.lower().endswith(".gguf"):
                continue
            fpath = os.path.join(dirpath, name)
            try:
                st = os.stat(fpath, follow_symlinks=False)
            except OSError:
                continue
            rec = {
                "path": fpath,
                "name": name,
                "size_bytes": st.st_size,
                "size_mib": int(st.st_size / 1024 / 1024),
                "atime": int(st.st_atime),
                "mtime": int(st.st_mtime),
                "dir_top": os.path.relpath(dirpath, base).split(os.sep)[0],
                "quant": _infer_quant_from_name(name),
            }
            header = parse_gguf_header(fpath)
            rec["is_gguf"] = header.get("is_gguf", False)
            if header.get("is_gguf"):
                rec["gguf_version"] = header.get("version")
                rec["tensor_count"] = header.get("tensor_count")
                rec["kv_count"] = header.get("metadata_kv_count")
            out.append(rec)
    return out


def find_size_collisions(lm_models: list,
                         hf_cache_dir: Optional[str] = None) -> list:
    """For every LM-Studio model, look up files in HF cache with the
    identical size. Returns list of {lm_path, hf_candidates: [...], size}.

    A size match is suggestive of duplication ; full SHA verification is
    delegated to the dedup planner (R&D #15.3)."""
    hf_root = hf_cache_dir or os.path.expanduser(_HF_CACHE_DIR)
    if not os.path.isdir(hf_root):
        return []
    # Index HF cache by size for fast lookup
    by_size: dict = {}
    for dirpath, _, files in os.walk(hf_root, followlinks=False):
        for fname in files:
            fp = os.path.join(dirpath, fname)
            try:
                size = os.path.getsize(fp)
            except OSError:
                continue
            if size < 100 * 1024 * 1024:   # skip tiny files
                continue
            by_size.setdefault(size, []).append(fp)
    matches: list = []
    for m in lm_models:
        size = m.get("size_bytes", 0)
        if size < 100 * 1024 * 1024:
            continue
        candidates = by_size.get(size, [])
        # Exclude self (in case LM-Studio is symlinked into HF cache)
        candidates = [c for c in candidates if c != m["path"]]
        if not candidates:
            continue
        matches.append({
            "lm_path": m["path"],
            "lm_name": m["name"],
            "size_bytes": size,
            "size_mib": m["size_mib"],
            "hf_candidates": candidates[:5],
        })
    return matches


def read_settings() -> dict:
    """Read ~/.lmstudio/settings.json for default model + ctx (best-effort)."""
    p = settings_path()
    if not os.path.exists(p):
        return {}
    try:
        import json
        with open(p) as f:
            d = json.load(f)
        return d if isinstance(d, dict) else {}
    except (OSError, ValueError):
        return {}


def status() -> dict:
    """Top-level inventory snapshot for the UI."""
    root = models_dir()
    if not os.path.isdir(root):
        return {
            "ok": True,
            "available": False,
            "reason": f"LM-Studio models dir not found at {root}",
            "models_dir": root,
        }
    models = scan_models(root)
    total_size_mib = sum(m["size_mib"] for m in models)
    matches = find_size_collisions(models)
    return {
        "ok": True,
        "available": True,
        "models_dir": root,
        "models_count": len(models),
        "total_size_gib": round(total_size_mib / 1024, 1),
        "models": sorted(models, key=lambda m: -m["size_mib"])[:50],
        "hf_size_matches": matches[:20],
        "duplication_suspect_count": len(matches),
        "duplication_suspect_gib": round(
            sum(m["size_bytes"] for m in matches) / 1e9, 2
        ),
    }
