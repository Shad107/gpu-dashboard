"""Module drm_fdinfo_engine_usage_audit — per-PID DRM client
VRAM attribution via fdinfo (R&D #92.4).

Three modules touch related surface but none parse the DRM-
specific fdinfo keys :

  * fdinfo_kinds_audit       — classifies anon_inode FDs
                               (eventfd/io_uring/epoll/
                               sync_file) ; explicitly skips
                               drm-engine/drm-memory keys.
  * vram_leak.py / vram_quota — aggregate VRAM via NVML
                               (no per-process attribution).
  * proc_smaps.py            — anon RSS, not GPU accounting.

This audit walks /proc/*/fdinfo/* looking for `drm-pdev`
lines (the marker of a kernel-side DRM client). Per the
kernel docs (Documentation/gpu/drm-usage-stats.rst) the
relevant keys are :

  drm-pdev: <pci-bdf>
  drm-client-id: <u64>
  drm-memory-vram: <N> [KiB|MiB|GiB]
  drm-memory-gtt:  <N> [KiB|MiB|GiB]
  drm-engine-<class>: <cycles> <capacity>

Verdicts (worst-first) :

  vram_overcommit_per_client  err   single PID holds > 90 %
                                    of total fdinfo-reported
                                    DRM VRAM (runaway).
  vram_top3_concentrated      warn  top 3 PIDs combined hold
                                    > 80 % of total VRAM.
  many_drm_clients            accent > 30 distinct DRM client
                                    IDs (lots of small
                                    clients, hard to attribute
                                    incidents).
  ok                          distribution looks reasonable.
  requires_root               most fdinfo files unreadable.
  unknown                     zero drm-pdev clients found
                              (no nvidia/amdgpu/i915 driver
                              loaded, or only virtio-gpu).

The proposed engine_starvation (warn) and zombie_gpu_client
(accent) verdicts were dropped — both require per-PID delta
tracking and process-state cross-checks that explode the
audit's complexity for marginal forensic value.

stdlib only.
"""
from __future__ import annotations

import os
import re
from typing import Optional

NAME = "drm_fdinfo_engine_usage_audit"

DEFAULT_PROC = "/proc"

# Thresholds.
_OVERCOMMIT_PCT = 0.90
_TOP3_PCT = 0.80
_MANY_CLIENTS_THRESHOLD = 30
# Require at least this many *readable* fdinfo files to
# avoid firing 'requires_root' on systems that genuinely
# have no DRM clients.
_MIN_READABLE_FOR_KNOWN = 50

_UNITS = {
    "B": 1,
    "KIB": 1024,
    "MIB": 1024 ** 2,
    "GIB": 1024 ** 3,
    "TIB": 1024 ** 4,
}


def _parse_size(value: str) -> Optional[int]:
    """Parse fdinfo size like '12345 KiB'."""
    if not value:
        return None
    parts = value.strip().split()
    if not parts:
        return None
    try:
        n = int(parts[0])
    except ValueError:
        return None
    if len(parts) == 1:
        return n
    unit = parts[1].upper()
    return n * _UNITS.get(unit, 1)


def parse_fdinfo_drm(text: str) -> Optional[dict]:
    """Return {'pdev':..., 'client_id':..., 'vram': bytes,
    'gtt': bytes} if this fdinfo describes a DRM client,
    else None."""
    if not text or "drm-pdev:" not in text:
        return None
    out = {"pdev": "", "client_id": None,
           "vram": 0, "gtt": 0}
    for line in text.splitlines():
        if ":" not in line:
            continue
        key, _, val = line.partition(":")
        key = key.strip()
        val = val.strip()
        if key == "drm-pdev":
            out["pdev"] = val
        elif key == "drm-client-id":
            try:
                out["client_id"] = int(val)
            except ValueError:
                pass
        elif key == "drm-memory-vram":
            s = _parse_size(val)
            if s is not None:
                out["vram"] = s
        elif key == "drm-memory-gtt":
            s = _parse_size(val)
            if s is not None:
                out["gtt"] = s
    return out


def walk_fdinfo(proc_root: str = DEFAULT_PROC) -> dict:
    """Walk /proc/*/fdinfo/*, return summary :

    {
      'pid_vram': {pid: bytes},
      'pid_clients': {pid: count},
      'total_vram': bytes,
      'total_clients': int,
      'readable_files': int,
      'unreadable_files': int,
    }
    """
    pid_vram: dict = {}
    pid_clients: dict = {}
    total_vram = 0
    total_clients = 0
    readable = 0
    unreadable = 0
    if not os.path.isdir(proc_root):
        return {"pid_vram": {}, "pid_clients": {},
                "total_vram": 0, "total_clients": 0,
                "readable_files": 0, "unreadable_files": 0}
    try:
        entries = os.listdir(proc_root)
    except OSError:
        return {"pid_vram": {}, "pid_clients": {},
                "total_vram": 0, "total_clients": 0,
                "readable_files": 0, "unreadable_files": 0}
    for name in entries:
        if not name.isdigit():
            continue
        fdinfo_dir = os.path.join(proc_root, name, "fdinfo")
        try:
            fd_names = os.listdir(fdinfo_dir)
        except (OSError, PermissionError):
            continue
        for fd in fd_names:
            path = os.path.join(fdinfo_dir, fd)
            try:
                with open(path, "r",
                          encoding="utf-8") as fh:
                    text = fh.read()
                readable += 1
            except (OSError, PermissionError):
                unreadable += 1
                continue
            client = parse_fdinfo_drm(text)
            if client is None:
                continue
            total_clients += 1
            total_vram += client["vram"]
            pid = int(name)
            pid_vram[pid] = (
                pid_vram.get(pid, 0) + client["vram"])
            pid_clients[pid] = pid_clients.get(pid, 0) + 1
    return {
        "pid_vram": pid_vram,
        "pid_clients": pid_clients,
        "total_vram": total_vram,
        "total_clients": total_clients,
        "readable_files": readable,
        "unreadable_files": unreadable,
    }


def classify(summary: dict) -> dict:
    total_clients = summary["total_clients"]
    readable = summary["readable_files"]
    unreadable = summary["unreadable_files"]

    # If almost everything came back permission-denied AND we
    # found no clients, distinguish requires_root from
    # genuinely-empty.
    if total_clients == 0:
        if (unreadable > 0
                and readable < _MIN_READABLE_FOR_KNOWN):
            return {
                "verdict": "requires_root",
                "reason": (
                    f"{unreadable} fdinfo files unreadable "
                    f"and only {readable} readable — re-run "
                    "as root to inspect other PIDs.")}
        return {"verdict": "unknown",
                "reason": (
                    "No DRM clients found in any readable "
                    "fdinfo. No nvidia/amdgpu/i915 driver "
                    "exposing drm-pdev keys ; or only "
                    "virtio-gpu (which doesn't emit fdinfo "
                    "stats).")}

    pid_vram = summary["pid_vram"]
    total_vram = summary["total_vram"]

    if total_vram > 0:
        ranked = sorted(
            pid_vram.items(), key=lambda kv: -kv[1])
        top_pid, top_bytes = ranked[0]
        top_pct = top_bytes / total_vram

        if top_pct > _OVERCOMMIT_PCT:
            return {
                "verdict": "vram_overcommit_per_client",
                "reason": (
                    f"PID {top_pid} holds "
                    f"{top_bytes / 2**30:.2f} GiB "
                    f"({100 * top_pct:.0f}% of fdinfo-"
                    "reported DRM VRAM) — likely runaway "
                    "GPU client."),
                "pid": top_pid,
                "bytes": top_bytes}

        top3 = sum(b for _, b in ranked[:3])
        top3_pct = top3 / total_vram
        if top3_pct > _TOP3_PCT:
            names = [p for p, _ in ranked[:3]]
            return {
                "verdict": "vram_top3_concentrated",
                "reason": (
                    f"Top 3 PIDs {names} hold "
                    f"{100 * top3_pct:.0f}% of fdinfo-"
                    "reported DRM VRAM."),
                "pids": names}

    if total_clients > _MANY_CLIENTS_THRESHOLD:
        return {
            "verdict": "many_drm_clients",
            "reason": (
                f"{total_clients} distinct DRM client(s) "
                "across all PIDs — incident attribution "
                "harder ; consider whether all are needed."),
            "client_count": total_clients}

    return {"verdict": "ok",
            "reason": (
                f"{total_clients} DRM client(s) ; total "
                f"fdinfo-reported VRAM = "
                f"{total_vram / 2**30:.2f} GiB — "
                "distribution healthy.")}


def status(config: Optional[dict] = None,
           proc_root: str = DEFAULT_PROC) -> dict:
    summary = walk_fdinfo(proc_root)
    verdict = classify(summary)
    return {
        "ok": verdict["verdict"] == "ok",
        "drm_client_count": summary["total_clients"],
        "total_vram_bytes": summary["total_vram"],
        "readable_files": summary["readable_files"],
        "unreadable_files": summary["unreadable_files"],
        "verdict": verdict,
    }
