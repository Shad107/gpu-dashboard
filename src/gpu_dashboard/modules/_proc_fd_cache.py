"""Shared /proc/<pid>/{fd, fdinfo}/* scan cache — Hardening #15.

Four modules independently walk /proc/<pid>/fdinfo/* per status() call:
  bpf_program_inventory_audit
  inotify_audit
  drm_fdinfo_engine_usage_audit
  fdinfo_kinds_audit

Each walk costs O(processes × fds_per_process) on every call —
roughly 1100 ms total on a small VM, multiple seconds on busy
desktops. This module deduplicates the scan across the four
callers within the same Python process via a short TTL cache.

API:

  scan_proc_fd(proc_root="/proc", ttl_s=1.0) → dict
      {pid_str: {"pid": int,
                  "fd_links": [(fd_str, target_or_None), ...],
                  "fdinfo": {fd_str: text_or_None}}}

  invalidate() — drop the cache (test hook).

Caching is process-local and only applies when ``proc_root`` is
the default ``/proc``. Tests using temp paths bypass the cache.

stdlib only.
"""
from __future__ import annotations

import os
import time
from typing import Dict, List, Optional, Tuple


_DEFAULT_PROC = "/proc"
# Hardening #15: TTL needs to span a full collection_profile_audit
# cycle so the four walkers — which fire alphabetically across the
# ~8 s sweep of 411 modules, not back-to-back — share one scan.
# 15 s is long enough for any reasonable cycle and short enough
# that user-triggered refreshes still see recent data.
_DEFAULT_TTL_S = 15.0


# (timestamp, cached_result) — process-local, never persisted.
_CACHE: Tuple[float, Optional[Dict[str, dict]]] = (0.0, None)


def invalidate() -> None:
    """Drop the cache. Tests call this to force a re-scan."""
    global _CACHE
    _CACHE = (0.0, None)


def _read_text(path: str) -> Optional[str]:
    try:
        with open(path, "r", encoding="utf-8",
                  errors="replace") as fh:
            return fh.read()
    except (OSError, PermissionError):
        return None


def _readlink(path: str) -> Optional[str]:
    try:
        return os.readlink(path)
    except (OSError, PermissionError):
        return None


def _scan_one_pid(proc_root: str, pid_str: str) -> dict:
    """Walk one PID's fd + fdinfo. Always returns a dict; missing
    or unreadable parts surface as empty containers."""
    fd_links: List[Tuple[str, Optional[str]]] = []
    fdinfo: Dict[str, Optional[str]] = {}
    fd_dir = os.path.join(proc_root, pid_str, "fd")
    fdinfo_dir = os.path.join(proc_root, pid_str, "fdinfo")
    try:
        fd_names = os.listdir(fd_dir)
    except OSError:
        fd_names = []
    for fd in fd_names:
        fd_links.append((fd, _readlink(os.path.join(fd_dir, fd))))
    try:
        fdinfo_names = os.listdir(fdinfo_dir)
    except OSError:
        fdinfo_names = []
    for fd in fdinfo_names:
        fdinfo[fd] = _read_text(os.path.join(fdinfo_dir, fd))
    try:
        pid_int = int(pid_str)
    except ValueError:
        pid_int = -1
    return {"pid": pid_int,
            "fd_links": fd_links,
            "fdinfo": fdinfo}


def _do_scan(proc_root: str) -> Dict[str, dict]:
    out: Dict[str, dict] = {}
    try:
        names = os.listdir(proc_root)
    except OSError:
        return out
    for name in names:
        if not name.isdigit():
            continue
        out[name] = _scan_one_pid(proc_root, name)
    return out


def scan_proc_fd(proc_root: str = _DEFAULT_PROC,
                   ttl_s: float = _DEFAULT_TTL_S) -> Dict[str, dict]:
    """Return a per-PID snapshot of /proc/<pid>/{fd, fdinfo}.

    Cached for ``ttl_s`` seconds when ``proc_root`` is the default
    ``/proc`` — across all callers in the same Python process,
    so the four walker modules pay the scan cost once per
    ``collection_profile_audit`` cycle.

    Pass a non-default ``proc_root`` (e.g. ``tmp_path`` in tests)
    to bypass the cache entirely.
    """
    global _CACHE
    if proc_root != _DEFAULT_PROC:
        return _do_scan(proc_root)
    now = time.monotonic()
    ts, data = _CACHE
    if data is not None and (now - ts) < ttl_s:
        return data
    result = _do_scan(proc_root)
    _CACHE = (now, result)
    return result
