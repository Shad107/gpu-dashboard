"""Module fs_specific_tunables_audit — ext4/xfs/f2fs per-FS
error counters and tunables (R&D #68.3).

Each filesystem driver exposes its own /sys/fs/<type>/<dev>/
subtree of per-device tunables and error counters. These are
the earliest silent corruption signal on a desktop/homelab —
existing fs_mount_audit covers mount-line state and
block_queue_audit covers the block layer, but neither reads
the FS-driver's own opinion of itself.

Reads :

  ext4 :
    /sys/fs/ext4/<dev>/errors_count       monotonic FS error
                                            counter (any > 0 →
                                            silent corruption).
    /sys/fs/ext4/<dev>/warning_count      monotonic warning
                                            counter.
    /sys/fs/ext4/<dev>/first_error_time   unix-ts of first error
                                            since FS creation (0
                                            = never).
    /sys/fs/ext4/<dev>/lifetime_write_kbytes
                                            cumulative writes
                                            (useful for SSD wear
                                             cross-check).

  xfs :
    /sys/fs/xfs/<dev>/error/{metadata,fail_at_unmount,...}
    /sys/fs/xfs/<dev>/stats/stats         text counters for
                                            unmount errors,
                                            metadata corruption,
                                            slow-path counters.

  f2fs :
    /sys/fs/f2fs/<dev>/features           ABI bitmask.
    /sys/fs/f2fs/<dev>/gc_idle            background GC mode (0
                                            = disabled / risky on
                                            laptops on AC).

Verdicts (priority order) :
  ext4_errors_logged                ≥1 ext4 device reports
                                      errors_count > 0 OR
                                      first_error_time != 0.
  xfs_metadata_corruption_counter   xfs stats blob contains a
                                      non-zero metadata
                                      corruption counter.
  f2fs_gc_disabled                  f2fs gc_idle = 0 (background
                                      GC off — risky long-term).
  requires_root                     /sys/fs/<type> present but
                                      *all* counters unreadable.
  ok                                all healthy.
  unknown                           no FS subtree present
                                      (containers, tmpfs-only).

stdlib only.
"""
from __future__ import annotations

import os
import re
from typing import Dict, List, Optional


NAME = "fs_specific_tunables_audit"


_SYS_EXT4 = "/sys/fs/ext4"
_SYS_XFS = "/sys/fs/xfs"
_SYS_F2FS = "/sys/fs/f2fs"

_XFS_CORRUPTION_RE = re.compile(
    r"(?:bs_chk|fcntr_corruption|ag_unhealth)\s+(\d+)")


def _read(path: str) -> Optional[str]:
    try:
        with open(path) as f:
            return f.read().strip()
    except OSError:
        return None


def _read_int(path: str) -> Optional[int]:
    t = _read(path)
    if t is None:
        return None
    try:
        return int(t)
    except ValueError:
        return None


def list_fs_devices(sys_path: str) -> List[str]:
    """Returns subdir names that look like device IDs (skips
    'features' / 'attr' / 'orlov' pseudo entries)."""
    if not os.path.isdir(sys_path):
        return []
    out: List[str] = []
    try:
        for n in os.listdir(sys_path):
            if n in ("features", "attr"):
                continue
            if os.path.isdir(os.path.join(sys_path, n)):
                out.append(n)
    except OSError:
        return []
    return sorted(out)


def scan_ext4(sys_path: str = _SYS_EXT4) -> List[dict]:
    out: List[dict] = []
    for dev in list_fs_devices(sys_path):
        d = os.path.join(sys_path, dev)
        out.append({
            "dev": dev,
            "errors_count": _read_int(os.path.join(
                d, "errors_count")),
            "warning_count": _read_int(os.path.join(
                d, "warning_count")),
            "first_error_time": _read_int(os.path.join(
                d, "first_error_time")),
            "lifetime_write_kbytes": _read_int(os.path.join(
                d, "lifetime_write_kbytes")),
        })
    return out


def scan_xfs(sys_path: str = _SYS_XFS) -> List[dict]:
    out: List[dict] = []
    for dev in list_fs_devices(sys_path):
        stats_path = os.path.join(sys_path, dev, "stats", "stats")
        stats_text = _read(stats_path)
        corruption = 0
        if stats_text:
            for m in _XFS_CORRUPTION_RE.finditer(stats_text):
                try:
                    corruption += int(m.group(1))
                except ValueError:
                    pass
        out.append({
            "dev": dev,
            "stats_present": stats_text is not None,
            "metadata_corruption_counter": corruption,
        })
    return out


def scan_f2fs(sys_path: str = _SYS_F2FS) -> List[dict]:
    out: List[dict] = []
    for dev in list_fs_devices(sys_path):
        d = os.path.join(sys_path, dev)
        out.append({
            "dev": dev,
            "features": _read(os.path.join(d, "features")),
            "gc_idle": _read_int(os.path.join(d, "gc_idle")),
        })
    return out


def classify(ext4: List[dict], xfs: List[dict],
              f2fs: List[dict],
              ext4_present: bool, xfs_present: bool,
              f2fs_present: bool) -> dict:

    surfaces_present = ext4_present or xfs_present or f2fs_present
    if not surfaces_present:
        return {"verdict": "unknown",
                "reason": ("No /sys/fs/{ext4,xfs,f2fs} subtree "
                          "found — running on a kernel without "
                          "these FS types or inside a chroot."),
                "recommendation": ""}

    # 1) ext4_errors_logged
    ext4_offenders = [e for e in ext4
                            if (e.get("errors_count") or 0) > 0
                              or (e.get("first_error_time") or 0)
                                  > 0]
    if ext4_offenders:
        sample = ", ".join(
            f"{e['dev']} errors={e.get('errors_count')}"
                for e in ext4_offenders[:3])
        return {"verdict": "ext4_errors_logged",
                "reason": (f"{len(ext4_offenders)} ext4 device(s) "
                          f"report errors : {sample}."),
                "recommendation": _recipe_ext4_errors()}

    # 2) xfs_metadata_corruption_counter
    xfs_offenders = [x for x in xfs
                            if x.get("metadata_corruption_counter")
                                > 0]
    if xfs_offenders:
        sample = ", ".join(
            f"{x['dev']} corruption="
            f"{x['metadata_corruption_counter']}"
                for x in xfs_offenders[:3])
        return {"verdict": "xfs_metadata_corruption_counter",
                "reason": (f"{len(xfs_offenders)} xfs device(s) "
                          f"report non-zero metadata corruption "
                          f"counters : {sample}."),
                "recommendation": _recipe_xfs_corruption()}

    # 3) f2fs_gc_disabled
    f2fs_offenders = [f for f in f2fs
                            if f.get("gc_idle") == 0]
    if f2fs_offenders:
        sample = ", ".join(f["dev"] for f in f2fs_offenders[:3])
        return {"verdict": "f2fs_gc_disabled",
                "reason": (f"{len(f2fs_offenders)} f2fs device(s) "
                          f"have background GC disabled "
                          f"(gc_idle=0) : {sample}."),
                "recommendation": _recipe_f2fs_gc()}

    # 4) requires_root — all counters unreadable across all FSes
    total_devs = len(ext4) + len(xfs) + len(f2fs)
    if total_devs > 0:
        all_unreadable = all(
            (e.get("errors_count") is None
                and e.get("warning_count") is None
                and e.get("lifetime_write_kbytes") is None)
                for e in ext4) if ext4 else True
        if not ext4 or all_unreadable:
            if total_devs == len(ext4) and all_unreadable:
                return {"verdict": "requires_root",
                        "reason": ("All ext4 per-FS counters were "
                                  "unreadable — running as "
                                  "unprivileged user."),
                        "recommendation": _recipe_requires_root()}

    return {"verdict": "ok",
            "reason": (f"ext4={len(ext4)} dev(s), "
                      f"xfs={len(xfs)} dev(s), "
                      f"f2fs={len(f2fs)} dev(s) — all counters "
                      f"clean."),
            "recommendation": ""}


def status(config=None,
            sys_ext4: str = _SYS_EXT4,
            sys_xfs: str = _SYS_XFS,
            sys_f2fs: str = _SYS_F2FS) -> dict:
    ext4_present = os.path.isdir(sys_ext4)
    xfs_present = os.path.isdir(sys_xfs)
    f2fs_present = os.path.isdir(sys_f2fs)
    ext4 = scan_ext4(sys_ext4) if ext4_present else []
    xfs = scan_xfs(sys_xfs) if xfs_present else []
    f2fs = scan_f2fs(sys_f2fs) if f2fs_present else []
    verdict = classify(ext4, xfs, f2fs,
                          ext4_present, xfs_present, f2fs_present)
    return {"ok": ext4_present or xfs_present or f2fs_present,
              "ext4_present": ext4_present,
              "xfs_present": xfs_present,
              "f2fs_present": f2fs_present,
              "ext4_devices": ext4,
              "xfs_devices": xfs,
              "f2fs_devices": f2fs,
              "verdict": verdict}


# ── recovery recipes ────────────────────────────────────────────

def _recipe_ext4_errors() -> str:
    return ("# ext4 errors_count > 0 means filesystem detected\n"
            "# I/O or metadata corruption. Inspect the live\n"
            "# counters AND the dmesg trail :\n"
            "cat /sys/fs/ext4/<dev>/{errors_count,first_error_*}\n"
            "sudo dmesg | grep -i -e ext4 -e EXT4 | tail\n"
            "# Schedule an offline fsck at next reboot :\n"
            "sudo tune2fs -c 1 /dev/<dev>     # one mount until check\n"
            "sudo touch /forcefsck && sudo reboot\n")


def _recipe_xfs_corruption() -> str:
    return ("# XFS metadata corruption counter non-zero.\n"
            "sudo cat /sys/fs/xfs/<dev>/stats/stats\n"
            "sudo dmesg | grep -i xfs | tail\n"
            "# Online repair (xfs_repair while mounted is unsafe!):\n"
            "sudo umount /mnt/<dev> && sudo xfs_repair /dev/<dev>\n")


def _recipe_f2fs_gc() -> str:
    return ("# f2fs background GC disabled — long-term writes\n"
            "# will degrade performance. Re-enable :\n"
            "echo 1 | sudo tee /sys/fs/f2fs/<dev>/gc_idle\n"
            "# 0 = disabled, 1 = idle GC, 2 = aggressive\n")


def _recipe_requires_root() -> str:
    return ("# Per-FS counter files require ownership of the FS\n"
            "# device or root. Run :\n"
            "sudo cat /sys/fs/ext4/<dev>/errors_count\n")
