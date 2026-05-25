"""Module fscache_cachefiles_audit — fscache + cachefiles
backend health (R&D #101.3).

FS-Cache (with the cachefiles backend) lets NFS / AFS / Ceph
mounts cache hot data on local disk. The classic homelab
failure mode : a remote model checkpoint dir is exported via
NFS, the client mounts with `fsc`, then either :

  - cachefilesd isn't running, so writes go straight through
    to the network and cold-path costs 20+ GB re-downloads ;
  - the cache wedges in CULLING / EXHAUSTED state and
    cookie lookups silently fail.

No existing module reads /proc/fs/fscache or /sys/fs/cachefiles.
nfs_mountstats_audit covers /proc/self/mountstats (RPC timings),
fs_mount_audit is generic mount options, bdi_writeback_audit
is writeback only.

Reads :

  /proc/fs/fscache/stats            (overall counters)
  /proc/fs/fscache/caches           (active backends)
  /proc/mounts                      (nfs mounts with `fsc`)
  /sys/module/fscache               (module presence)

Verdicts (worst-first) :

  fscache_caches_culling      err     a registered cache is
                                      in CULLING/EXHAUSTED —
                                      lookups silently fail.
  nfs_fsc_without_backend     warn    NFS mount option `fsc`
                                      but no active cachefiles
                                      backend.
  fscache_loaded_no_caches    accent  fscache module loaded
                                      but no caches registered
                                      — dead-weight modprobe.
  ok                                  cache active, caching
                                      mounts have backend.
  requires_root                       /proc/fs/fscache exists
                                      but unreadable.
  unknown                             fscache not built /
                                      not loaded.

stdlib only.
"""
from __future__ import annotations

import os
from typing import Optional

NAME = "fscache_cachefiles_audit"

DEFAULT_FSCACHE_PROC = "/proc/fs/fscache"
DEFAULT_CACHEFILES_SYSFS = "/sys/fs/cachefiles"
DEFAULT_FSCACHE_MODULE = "/sys/module/fscache"
DEFAULT_PROC_MOUNTS = "/proc/mounts"


def _read_text(path: str) -> Optional[str]:
    try:
        with open(path, "r", encoding="utf-8") as fh:
            return fh.read()
    except (OSError, PermissionError):
        return None


def parse_caches(text: Optional[str]) -> list:
    """Parse /proc/fs/fscache/caches.

    Format varies by kernel ; rows like :
       CACHE   STATE   NAME
       SSD     ACTIVE  default
    Return list of {name, state}.
    """
    out: list = []
    if not text:
        return out
    for line in text.splitlines():
        parts = line.split()
        if len(parts) < 2:
            continue
        if parts[0].upper() in ("CACHE", "TAG"):
            continue
        out.append({"name": parts[-1].lower(),
                    "state": parts[1].upper()})
    return out


def parse_nfs_fsc_mounts(text: Optional[str]) -> list:
    """Return list of nfs mountpoints with `fsc` option."""
    out: list = []
    if not text:
        return out
    for line in text.splitlines():
        parts = line.split()
        if len(parts) < 4:
            continue
        if parts[2] not in ("nfs", "nfs4"):
            continue
        opts = parts[3].split(",")
        if "fsc" in opts:
            out.append(parts[1])
    return out


def classify(module_present: bool,
             proc_present: bool,
             proc_readable: bool,
             caches: list,
             nfs_fsc_mounts: list,
             cachefiles_present: bool) -> dict:
    if not module_present:
        return {"verdict": "unknown",
                "reason": (
                    "fscache module not loaded / not "
                    "built — nothing to audit.")}
    if proc_present and not proc_readable:
        return {"verdict": "requires_root",
                "reason": (
                    "/proc/fs/fscache exists but "
                    "unreadable — re-run as root.")}

    # err — any cache in CULLING/EXHAUSTED
    bad = [c for c in caches
           if c["state"] in ("CULLING", "EXHAUSTED",
                              "DEAD")]
    if bad:
        names = [
            f"{c['name']}({c['state']})" for c in bad]
        return {
            "verdict": "fscache_caches_culling",
            "reason": (
                f"{len(bad)} fscache cache(s) in "
                f"bad state: {names}. Cookie lookups "
                "are silently failing — reads going "
                "to the network.")}

    # warn — NFS fsc mount without cachefiles backend
    if nfs_fsc_mounts and not cachefiles_present:
        return {
            "verdict": "nfs_fsc_without_backend",
            "reason": (
                f"{len(nfs_fsc_mounts)} NFS mount(s) "
                f"use 'fsc' option but no /sys/fs/"
                f"cachefiles backend registered. "
                "Cache is a no-op — every read pulls "
                "fresh from the network.")}

    # accent — fscache loaded but no caches
    if not caches:
        return {
            "verdict": "fscache_loaded_no_caches",
            "reason": (
                "fscache module loaded but no caches "
                "registered. Dead-weight modprobe ; "
                "unload or set up cachefilesd.")}

    return {"verdict": "ok",
            "reason": (
                f"{len(caches)} cache(s) active ; "
                f"{len(nfs_fsc_mounts)} NFS mount(s) "
                "with fsc, backend present.")}


def status(config: Optional[dict] = None,
           fscache_proc: str = DEFAULT_FSCACHE_PROC,
           cachefiles_sysfs: str = DEFAULT_CACHEFILES_SYSFS,
           fscache_module: str = DEFAULT_FSCACHE_MODULE,
           proc_mounts: str = DEFAULT_PROC_MOUNTS) -> dict:
    module_present = (
        os.path.isdir(fscache_module)
        or os.path.isdir(fscache_proc))
    proc_present = os.path.isdir(fscache_proc)
    caches_text = _read_text(
        os.path.join(fscache_proc, "caches"))
    proc_readable = caches_text is not None
    caches = parse_caches(caches_text)
    nfs_fsc_mounts = parse_nfs_fsc_mounts(
        _read_text(proc_mounts))
    cachefiles_present = os.path.isdir(cachefiles_sysfs)

    verdict = classify(
        module_present, proc_present, proc_readable,
        caches, nfs_fsc_mounts, cachefiles_present)
    return {
        "ok": verdict["verdict"] == "ok",
        "module_loaded": module_present,
        "cache_count": len(caches),
        "nfs_fsc_mount_count": len(nfs_fsc_mounts),
        "cachefiles_backend_present": cachefiles_present,
        "verdict": verdict,
    }
