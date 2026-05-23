"""Module sd_cache_janitor — Stable-Diffusion / ComfyUI cache janitor (R&D #21.5).

LLM rigs that also run image diffusion (A1111, ComfyUI, InvokeAI,
Fooocus) accumulate 50-200 GB of stale artifacts — models nobody
loaded in 3 months, outputs from one-off batches, temp tiles never
cleaned up. This module extends the HF Janitor (R&D #11) pattern to
known SD/Comfy paths.

Read-only — same safety contract as HF Janitor : it reports total /
cold size + a top-N candidate list, never deletes.

Default 'cold' threshold = 30 days unmodified ; override via
SD_CACHE_COLD_DAYS config key.

Cached scan: full-tree os.walk can be slow with large model trees,
so we cache results for 5 minutes per directory.

stdlib only.
"""
from __future__ import annotations

import os
import time
from typing import Optional


NAME = "sd_cache_janitor"


# Directories scanned (relative to $HOME or absolute).
DEFAULT_TARGETS = [
    # PyTorch cache
    "~/.cache/torch",
    # ComfyUI standard install paths
    "~/ComfyUI/models",
    "~/ComfyUI/output",
    "~/ComfyUI/input",
    "~/ComfyUI/temp",
    "~/comfyui/models",
    "~/comfyui/output",
    # AUTOMATIC1111 / stable-diffusion-webui
    "~/stable-diffusion-webui/models",
    "~/stable-diffusion-webui/outputs",
    "~/stable-diffusion-webui/log",
    # InvokeAI
    "~/invokeai/outputs",
    "~/invokeai/models",
    # Fooocus
    "~/Fooocus/models",
    "~/Fooocus/outputs",
    # SwarmUI
    "~/SwarmUI/Output",
    "~/SwarmUI/Models",
]


_SCAN_CACHE: dict = {}
_SCAN_CACHE_TTL_S = 300


def _expand(p: str) -> str:
    return os.path.expanduser(p)


def existing_targets(targets: Optional[list[str]] = None) -> list[str]:
    """Filter the configured target list down to dirs that actually exist."""
    out: list[str] = []
    for t in (targets or DEFAULT_TARGETS):
        p = _expand(t)
        if os.path.isdir(p):
            out.append(p)
    return out


def scan_dir(path: str, cold_age_s: int = 30 * 86400,
              now_ts: Optional[float] = None) -> dict:
    """Walk a directory tree. Returns :
       {path, total_bytes, file_count, cold_bytes, cold_count,
        oldest_ts, sample_old_files: [{path, size, age_days}]}.

    Cached for 5 minutes per path.
    """
    if now_ts is None:
        now_ts = time.time()
    cached = _SCAN_CACHE.get(path)
    if cached and cached["fetched_at"] + _SCAN_CACHE_TTL_S > now_ts:
        return cached["result"]
    total_bytes = 0
    file_count = 0
    cold_bytes = 0
    cold_count = 0
    oldest_ts: Optional[float] = None
    old_files: list[dict] = []
    try:
        for root, dirs, files in os.walk(path, followlinks=False):
            for name in files:
                fp = os.path.join(root, name)
                try:
                    st = os.lstat(fp)
                except OSError:
                    continue
                if not _is_regular(st):
                    continue
                size = st.st_size
                age_s = now_ts - st.st_mtime
                total_bytes += size
                file_count += 1
                if oldest_ts is None or st.st_mtime < oldest_ts:
                    oldest_ts = st.st_mtime
                if age_s >= cold_age_s:
                    cold_bytes += size
                    cold_count += 1
                    if size > 100 * 1024 * 1024:  # >100 MiB worth showing
                        old_files.append({
                            "path": fp,
                            "size_mib": round(size / 1024 ** 2, 1),
                            "age_days": int(age_s / 86400),
                        })
    except OSError:
        pass
    old_files.sort(key=lambda r: -r["size_mib"])
    result = {
        "path": path,
        "total_bytes": total_bytes,
        "total_mib": round(total_bytes / 1024 ** 2, 1),
        "file_count": file_count,
        "cold_bytes": cold_bytes,
        "cold_mib": round(cold_bytes / 1024 ** 2, 1),
        "cold_count": cold_count,
        "oldest_ts": int(oldest_ts) if oldest_ts is not None else None,
        "sample_old_files": old_files[:10],
    }
    _SCAN_CACHE[path] = {"fetched_at": now_ts, "result": result}
    return result


def _is_regular(st) -> bool:
    """Skip symlinks, sockets, etc."""
    import stat
    return stat.S_ISREG(st.st_mode)


def status(cfg=None) -> dict:
    """Aggregate snapshot. Lazy : only scans dirs that exist on this system."""
    cold_age_s = 30 * 86400
    if cfg:
        try:
            cold_age_s = int(cfg.get("SD_CACHE_COLD_DAYS", "30")) * 86400
        except (ValueError, TypeError):
            pass
    targets = existing_targets()
    scans = [scan_dir(p, cold_age_s) for p in targets]
    total_bytes = sum(s["total_bytes"] for s in scans)
    cold_bytes = sum(s["cold_bytes"] for s in scans)
    # Top old-file candidates across all dirs
    all_old: list[dict] = []
    for s in scans:
        all_old.extend(s["sample_old_files"])
    all_old.sort(key=lambda r: -r["size_mib"])
    return {
        "ok": True,
        "scanned_dirs": targets,
        "scanned_count": len(targets),
        "total_bytes": total_bytes,
        "total_gib": round(total_bytes / 1024 ** 3, 2),
        "cold_bytes": cold_bytes,
        "cold_gib": round(cold_bytes / 1024 ** 3, 2),
        "cold_age_days": cold_age_s // 86400,
        "per_dir": scans,
        "top_candidates": all_old[:20],
    }
