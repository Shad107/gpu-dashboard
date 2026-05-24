"""Module btrfs_allocator_audit — btrfs chunk allocator
pressure check (R&D #80.3).

Reads /sys/fs/btrfs/<uuid>/allocation/{data,metadata,system}/
<profile>/{disk_total,disk_used,bytes_used,total_bytes} to
catch the classic btrfs footgun :

  *  data chunks have plenty of free space
  *  metadata chunks are full
  *  every write fails with ENOSPC even though `df` shows
     hundreds of GB free

This happens when btrfs has allocated all available device
space into data chunks and the metadata chunks are tight.
The fix is `btrfs balance start -musage=…` BEFORE the disk
fills — once metadata is 100 % full the rebalance itself can
fail.

Tree structure under /sys/fs/btrfs/<uuid>/allocation/<type>/
<profile>/ :

  bytes_used       bytes actually written within allocated
                   chunks
  disk_total       bytes of disk allocated to this profile
  disk_used        bytes the kernel reports used on disk
  total_bytes      total bytes available in allocated chunks

profile is one of  single / dup / raid0 / raid1 / raid1c3 /
raid1c4 / raid5 / raid6 / raid10.

Verdicts (worst first) :

  metadata_full_imminent  any metadata profile with
                          bytes_used / total_bytes > 90 %
                          AND total_bytes > 100 MiB.
  unbalanced_chunks       data disk_total - bytes_used
                          > 5 GiB  →  large allocated-
                          but-unused holes inside chunks.
  mixed_profile_unexpected  ≥2 profile dirs under data or
                            metadata (multi-profile setup
                            on a single-disk homelab is
                            usually drift).
  ok                      everything healthy.
  n/a                     btrfs kernel module loaded but
                          no btrfs filesystem mounted —
                          only /sys/fs/btrfs/features/.
  unknown                 /sys/fs/btrfs/ absent entirely.
"""
from __future__ import annotations

import os
from typing import Optional

DEFAULT_BTRFS_ROOT = "/sys/fs/btrfs"

# Thresholds
_METADATA_FULL_RATIO = 0.90
_METADATA_MIN_BYTES = 100 * 1024 * 1024   # 100 MiB
_UNBALANCED_DATA_HOLE = 5 * 1024 * 1024 * 1024   # 5 GiB

_KNOWN_TYPES = ("data", "metadata", "system")
_KNOWN_PROFILES = (
    "single", "dup", "raid0", "raid1", "raid1c3", "raid1c4",
    "raid5", "raid6", "raid10")


def _read_int(path: str) -> Optional[int]:
    try:
        with open(path, "r", encoding="utf-8") as fh:
            return int(fh.read().strip())
    except (OSError, PermissionError, ValueError):
        return None


def list_filesystems(root: str = DEFAULT_BTRFS_ROOT
                      ) -> list[str]:
    """Returns UUIDs of mounted btrfs filesystems
    (excludes the `features` pseudo-dir).
    """
    try:
        entries = os.listdir(root)
    except OSError:
        return []
    return sorted(
        e for e in entries
        if e != "features"
        and os.path.isdir(os.path.join(root, e, "allocation")))


def read_fs(root: str, uuid: str) -> dict:
    """Returns nested dict {type: {profile: {key: int}}}."""
    out: dict = {}
    base = os.path.join(root, uuid, "allocation")
    for t in _KNOWN_TYPES:
        tdir = os.path.join(base, t)
        if not os.path.isdir(tdir):
            continue
        out[t] = {}
        try:
            profiles = os.listdir(tdir)
        except OSError:
            continue
        for prof in profiles:
            pdir = os.path.join(tdir, prof)
            if not os.path.isdir(pdir):
                continue
            out[t][prof] = {
                "bytes_used": _read_int(
                    os.path.join(pdir, "bytes_used")),
                "disk_total": _read_int(
                    os.path.join(pdir, "disk_total")),
                "disk_used": _read_int(
                    os.path.join(pdir, "disk_used")),
                "total_bytes": _read_int(
                    os.path.join(pdir, "total_bytes")),
            }
    return out


def classify(root_exists: bool,
             filesystems: list[dict]) -> dict:
    if not root_exists:
        return {"verdict": "unknown",
                "reason": "/sys/fs/btrfs absent."}
    if not filesystems:
        return {"verdict": "n/a",
                "reason": (
                    "btrfs kernel module loaded but no "
                    "btrfs filesystems mounted.")}

    for fs in filesystems:
        uuid = fs["uuid"]
        types = fs["allocation"]
        # 1. err — metadata profile near full
        for prof, vals in types.get("metadata", {}).items():
            total = vals.get("total_bytes") or 0
            used = vals.get("bytes_used") or 0
            if (total > _METADATA_MIN_BYTES
                    and used / total > _METADATA_FULL_RATIO):
                return {
                    "verdict": "metadata_full_imminent",
                    "reason": (
                        f"metadata/{prof} on {uuid[:8]} is "
                        f"{used/total:.0%} full "
                        f"({used // (1024*1024)} MiB of "
                        f"{total // (1024*1024)} MiB)."),
                    "uuid": uuid, "profile": prof,
                    "ratio": used / total}

    for fs in filesystems:
        uuid = fs["uuid"]
        types = fs["allocation"]
        # 2. warn — data chunks under-utilized
        for prof, vals in types.get("data", {}).items():
            disk_total = vals.get("disk_total") or 0
            used = vals.get("bytes_used") or 0
            if disk_total - used > _UNBALANCED_DATA_HOLE:
                return {
                    "verdict": "unbalanced_chunks",
                    "reason": (
                        f"data/{prof} on {uuid[:8]} has "
                        f"{(disk_total-used)//(1024**3)} GiB "
                        "allocated but unused inside chunks."),
                    "uuid": uuid, "profile": prof,
                    "hole_bytes": disk_total - used}

    for fs in filesystems:
        uuid = fs["uuid"]
        types = fs["allocation"]
        # 3. accent — unexpected multi-profile under data/metadata
        for t in ("data", "metadata"):
            profiles = list(types.get(t, {}).keys())
            if len(profiles) > 1:
                return {
                    "verdict": "mixed_profile_unexpected",
                    "reason": (
                        f"{t} block group on {uuid[:8]} has "
                        f"{len(profiles)} profiles "
                        f"({','.join(profiles)}) — drift "
                        "from a balance/convert."),
                    "uuid": uuid, "type": t,
                    "profiles": profiles}

    return {"verdict": "ok",
            "reason": (
                f"{len(filesystems)} btrfs filesystem(s) "
                "audited ; metadata + data healthy.")}


def status(config: Optional[dict] = None,
           root: str = DEFAULT_BTRFS_ROOT) -> dict:
    root_exists = os.path.isdir(root)
    uuids = list_filesystems(root)
    filesystems = []
    for uuid in uuids:
        filesystems.append({
            "uuid": uuid,
            "allocation": read_fs(root, uuid),
        })
    verdict = classify(root_exists, filesystems)
    return {
        "ok": verdict["verdict"] not in (
            "unknown", "metadata_full_imminent"),
        "fs_count": len(filesystems),
        "filesystems": filesystems,
        "verdict": verdict,
    }
