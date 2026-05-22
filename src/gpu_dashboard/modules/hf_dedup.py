"""Module hf_dedup — Hugging Face cache deduplicator (R&D #15.3).

Same-content blobs appear multiple times in HF cache when users :
  - Re-download the same model under a different repo (fork / fine-tune)
  - Have multiple HF cache locations symlinked or duplicated
  - Snapshot vs blob storage layouts overlap

This module finds those duplicates and replaces redundant copies with
hardlinks (same filesystem) — zero data loss, instant reclaim.

Pipeline :
  1. Walk all configured cache dirs (HF + Ollama + user-supplied) for
     files > 50 MiB.
  2. Group by file size (fast).
  3. For each size collision, compute SHA-256 → find true duplicates.
  4. Build a dedup plan : keep the file with the most hardlinks already
     pointing to it (or the first one if none) ; the rest become hardlinks.
  5. Plan is RETURNED but NOT executed unless the caller explicitly
     confirms (avoids surprise data loss if hardlink fails).
  6. Cross-FS pairs are flagged and skipped (or symlinked if requested).

Stdlib only : os + hashlib + glob.
"""
from __future__ import annotations

import hashlib
import json
import os
import time
from typing import Optional


NAME = "hf_dedup"

# Minimum file size to consider for dedup (don't waste time on tiny metadata)
_MIN_SIZE_BYTES = 50 * 1024 * 1024  # 50 MiB

# Reports persist here (audit trail of dedup actions)
_REPORTS_DIR = "~/.config/gpu-dashboard/hf_dedup_reports"
_DEFAULT_DIRS = (
    "~/.cache/huggingface",
    "~/.ollama/models",
    "~/.cache/llama.cpp",
)


def reports_dir() -> str:
    return os.path.expanduser(_REPORTS_DIR)


def _expand_paths(extra_dirs: Optional[list] = None) -> list:
    paths = list(_DEFAULT_DIRS)
    if extra_dirs:
        paths.extend(extra_dirs)
    return [os.path.expanduser(p) for p in paths if os.path.isdir(os.path.expanduser(p))]


def _hash_file(path: str, chunk: int = 1024 * 1024) -> Optional[str]:
    """SHA-256 of a file. Returns None on read error."""
    h = hashlib.sha256()
    try:
        with open(path, "rb") as f:
            while True:
                buf = f.read(chunk)
                if not buf:
                    break
                h.update(buf)
    except OSError:
        return None
    return h.hexdigest()


def _scan_for_files(roots: list, min_size: int = _MIN_SIZE_BYTES) -> list:
    """Walk roots, return list of {path, size, inode, device}."""
    out: list = []
    for root in roots:
        for dirpath, _, files in os.walk(root, followlinks=False):
            for name in files:
                fpath = os.path.join(dirpath, name)
                try:
                    st = os.stat(fpath, follow_symlinks=False)
                except OSError:
                    continue
                if st.st_size < min_size:
                    continue
                out.append({
                    "path": fpath,
                    "size": st.st_size,
                    "inode": st.st_ino,
                    "device": st.st_dev,
                    "nlinks": st.st_nlink,
                })
    return out


def build_plan(extra_dirs: Optional[list] = None,
               min_size: int = _MIN_SIZE_BYTES) -> dict:
    """Scan + hash duplicates. Returns a dedup plan (no side effects).

    Returns :
      {ok, files_scanned, candidates_groups: [...], total_dupe_bytes,
       total_reclaim_bytes, plan: [{keep, replace, size, sha}]}

    'keep' is the canonical path ; 'replace' is the path that will become
    a hardlink to 'keep'. Cross-device pairs are skipped (flagged).
    """
    roots = _expand_paths(extra_dirs)
    if not roots:
        return {"ok": True, "available": False, "reason": "no cache dirs found"}
    files = _scan_for_files(roots, min_size=min_size)

    # 1. Group by size (cheap)
    by_size: dict = {}
    for f in files:
        by_size.setdefault(f["size"], []).append(f)

    groups: list = []
    plan: list = []
    total_dupe_bytes = 0
    cross_device_skipped: list = []
    for size, members in by_size.items():
        if len(members) < 2:
            continue
        # 2. Hash each member (only do this work on size-collisions)
        by_hash: dict = {}
        for m in members:
            sha = _hash_file(m["path"])
            if sha is None:
                continue
            by_hash.setdefault(sha, []).append(m)
        for sha, paths in by_hash.items():
            if len(paths) < 2:
                continue
            # Skip if all members already share the same inode (already deduped)
            inodes = {(p["device"], p["inode"]) for p in paths}
            if len(inodes) == 1:
                continue
            # Pick canonical : highest nlinks ; tiebreak shortest path
            canonical = max(paths, key=lambda p: (p["nlinks"], -len(p["path"])))
            for p in paths:
                if (p["device"], p["inode"]) == (canonical["device"], canonical["inode"]):
                    continue
                if p["device"] != canonical["device"]:
                    cross_device_skipped.append({
                        "keep": canonical["path"], "replace": p["path"],
                        "size": size, "reason": "cross-device",
                    })
                    continue
                plan.append({
                    "keep": canonical["path"], "replace": p["path"],
                    "size": size, "sha": sha[:16],
                })
                total_dupe_bytes += size
            groups.append({
                "size_bytes": size, "sha": sha[:16], "count": len(paths),
                "paths": [p["path"] for p in paths],
            })

    return {
        "ok": True,
        "available": True,
        "scanned_dirs": roots,
        "files_scanned": len(files),
        "duplicate_groups": len(groups),
        "groups": groups[:50],   # cap response size
        "plan": plan,
        "total_dupe_bytes": total_dupe_bytes,
        "reclaim_mib": round(total_dupe_bytes / 1024 / 1024, 1),
        "cross_device_skipped": cross_device_skipped,
    }


def execute_plan(plan: list, dry_run: bool = True) -> dict:
    """Apply a dedup plan (typically the 'plan' field returned by build_plan).

    For each {keep, replace} pair :
      - If dry_run : just count.
      - Else : os.unlink(replace) + os.link(keep, replace).
      - On failure : log + skip (don't crash the rest of the plan).

    Returns {applied, errors[], dry_run, bytes_reclaimed}.
    """
    applied = 0
    bytes_reclaimed = 0
    errors: list = []
    for step in plan:
        keep = step.get("keep")
        replace = step.get("replace")
        size = int(step.get("size", 0))
        if not keep or not replace:
            errors.append({"step": step, "error": "missing keep or replace"})
            continue
        if dry_run:
            applied += 1
            bytes_reclaimed += size
            continue
        # Best-effort hardlink replacement, atomic-ish via tmpname rename
        tmp = replace + ".dedup-tmp"
        try:
            os.link(keep, tmp)              # create hardlink with temp name
            os.replace(tmp, replace)         # atomic swap (overwrites old)
            applied += 1
            bytes_reclaimed += size
        except OSError as e:
            errors.append({"step": step, "error": str(e)})
            # Best-effort cleanup of leftover tmp
            try:
                if os.path.exists(tmp):
                    os.remove(tmp)
            except OSError:
                pass
    return {
        "ok": not errors,
        "dry_run": dry_run,
        "applied": applied,
        "errors": errors,
        "bytes_reclaimed": bytes_reclaimed,
        "reclaim_mib": round(bytes_reclaimed / 1024 / 1024, 1),
    }


def save_report(report: dict) -> str:
    """Write a dedup report (build_plan + execute_plan output) to the
    reports directory. Returns the path written."""
    d = reports_dir()
    os.makedirs(d, exist_ok=True)
    ts = int(time.time())
    p = os.path.join(d, f"dedup-{ts}.json")
    with open(p, "w") as f:
        json.dump(report, f, indent=2)
    return p


def list_reports(limit: int = 10) -> list:
    """Return recent report filenames (newest first)."""
    d = reports_dir()
    if not os.path.isdir(d):
        return []
    files = sorted(
        [os.path.join(d, f) for f in os.listdir(d) if f.endswith(".json")],
        reverse=True,
    )
    return files[:limit]
