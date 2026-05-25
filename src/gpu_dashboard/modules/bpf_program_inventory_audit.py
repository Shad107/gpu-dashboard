"""Module bpf_program_inventory_audit — pinned BPF objects
+ user-side fdinfo scan (R&D #81.2).

Existing btf_bpf_audit checks BTF availability and merely
counts /sys/fs/bpf/ entries.  This audit goes deeper :

  * lists pinned BPF objects under /sys/fs/bpf/ recursively
    (when readable),
  * scans /proc/<pid>/fdinfo/* for ``prog_id:`` / ``map_id:``
    lines which the kernel populates for BPF file descriptors,
    giving a user-side view of who is holding what.

This catches forgotten bpftrace pins, runaway systemd-oomd
churn, and orphan Cilium / Tetragon maps that keep leaking
memory because nobody owns the pin any more.

The BPF pin filesystem is mode 700 (root-only) on almost
every distro, so for a user-mode dashboard the dominant
verdict will be ``requires_root`` — we surface that
explicitly with a clear "re-run as root for the full
inventory" hint.

Verdicts (worst first) :

  excessive_pins                 > 50 pinned BPF objects
                                 under /sys/fs/bpf — runaway
                                 tracer or Cilium leak.
  many_user_prog_refs            > 50 unique prog/map ids
                                 referenced from user-mode
                                 fdinfo — unusual without a
                                 BPF tool open.
  pins_present                   pins or refs exist, count
                                 within sane bounds (accent,
                                 informational).
  ok_empty                       no BPF pins / refs visible.
  requires_root                  /sys/fs/bpf mounted but not
                                 readable as this UID — full
                                 inventory needs root.
  unknown                        /sys/fs/bpf not mounted.
"""
from __future__ import annotations

import os
import re
from typing import Optional

DEFAULT_BPF_FS = "/sys/fs/bpf"
DEFAULT_PROC = "/proc"
DEFAULT_MOUNTS = "/proc/mounts"

# Honored by collection_profile_audit (hardening #13): walks
# /proc/<pid>/fdinfo/* for every PID. Cost is O(processes ×
# fds-per-process) — ~500 ms on a small VM, multiple seconds on a
# desktop with browsers + IDEs running. There is no way to surface
# the BPF user-mode reference inventory without this walk; not
# optimizable in isolation. A future shared /proc fdinfo cache
# across modules (bpf, inotify, drm_fdinfo, fdinfo_kinds) would
# amortize the cost — deferred.
EXPECTED_SLOW = True

# Thresholds
_PIN_OVERFLOW = 50
_USER_REF_OVERFLOW = 50

_FDINFO_PROG = re.compile(r"^prog_id:\s*(\d+)\s*$")
_FDINFO_MAP = re.compile(r"^map_id:\s*(\d+)\s*$")


def _read_text(path: str) -> Optional[str]:
    try:
        with open(path, "r", encoding="utf-8") as fh:
            return fh.read()
    except (OSError, PermissionError):
        return None


def is_bpffs_mounted(mounts: str = DEFAULT_MOUNTS,
                      bpf_fs: str = DEFAULT_BPF_FS) -> bool:
    text = _read_text(mounts)
    if text is None:
        return False
    for line in text.splitlines():
        parts = line.split()
        if len(parts) >= 3 and parts[1] == bpf_fs and parts[2] == "bpf":
            return True
    return False


def list_pins(bpf_fs: str = DEFAULT_BPF_FS) -> tuple[Optional[int], bool]:
    """Returns (count, readable). count is None when fs is
    unreadable. readable=False distinguishes EACCES from
    the not-mounted case."""
    if not os.path.isdir(bpf_fs):
        return (None, False)
    count = 0
    readable = True
    try:
        for dirpath, dirnames, filenames in os.walk(
                bpf_fs, followlinks=False):
            count += len(filenames) + len(dirnames)
    except (OSError, PermissionError):
        # os.walk swallows per-dir EACCES — the outermost
        # listing failure raises ; treat as unreadable.
        readable = False
        return (None, readable)
    # Even when listdir on bpf_fs root works, sub-dirs may
    # be unreadable. We approximate "readable" by whether
    # we got past the outer dir at all.
    try:
        os.listdir(bpf_fs)
    except (OSError, PermissionError):
        readable = False
        return (None, readable)
    return (count, readable)


def scan_user_fdinfo(proc_root: str = DEFAULT_PROC
                       ) -> tuple[set[int], set[int], int]:
    """Walks /proc/<pid>/fdinfo/* for prog_id / map_id
    lines that only appear on BPF file descriptors.

    Returns (prog_ids, map_ids, pids_scanned)."""
    prog_ids: set[int] = set()
    map_ids: set[int] = set()
    pids_scanned = 0
    try:
        pid_entries = os.listdir(proc_root)
    except OSError:
        return (prog_ids, map_ids, 0)
    for name in pid_entries:
        if not name.isdigit():
            continue
        fdinfo_dir = os.path.join(proc_root, name, "fdinfo")
        try:
            fd_entries = os.listdir(fdinfo_dir)
        except (OSError, PermissionError):
            continue
        pids_scanned += 1
        for fd in fd_entries:
            path = os.path.join(fdinfo_dir, fd)
            text = _read_text(path)
            if text is None:
                continue
            for line in text.splitlines():
                m = _FDINFO_PROG.match(line)
                if m:
                    prog_ids.add(int(m.group(1)))
                    continue
                m = _FDINFO_MAP.match(line)
                if m:
                    map_ids.add(int(m.group(1)))
    return (prog_ids, map_ids, pids_scanned)


def classify(mounted: bool,
             pin_count: Optional[int], readable: bool,
             prog_ids: set[int], map_ids: set[int],
             pids_scanned: int) -> dict:
    if not mounted:
        return {"verdict": "unknown",
                "reason": "BPF filesystem not mounted at "
                          "/sys/fs/bpf."}

    user_ref_count = len(prog_ids) + len(map_ids)

    # 1. err — excessive pins (only computable when readable)
    if pin_count is not None and pin_count > _PIN_OVERFLOW:
        return {"verdict": "excessive_pins",
                "reason": (
                    f"{pin_count} pinned BPF objects under "
                    "/sys/fs/bpf — runaway tracer or leaked "
                    "Cilium maps."),
                "pin_count": pin_count}

    # 2. warn — many user-mode prog/map refs
    if user_ref_count > _USER_REF_OVERFLOW:
        return {"verdict": "many_user_prog_refs",
                "reason": (
                    f"{len(prog_ids)} unique prog_id and "
                    f"{len(map_ids)} unique map_id ids "
                    "referenced by user-mode fdinfo — "
                    "unusual without a BPF tool open."),
                "prog_count": len(prog_ids),
                "map_count": len(map_ids)}

    # 3. accent — pins or refs present
    if (pin_count is not None and pin_count > 0
            or user_ref_count > 0):
        return {"verdict": "pins_present",
                "reason": (
                    f"{pin_count if pin_count is not None else 0}"
                    " pin(s) ; "
                    f"{len(prog_ids)} prog ref(s), "
                    f"{len(map_ids)} map ref(s) from "
                    f"{pids_scanned} user PID(s)."),
                "pin_count": pin_count,
                "prog_count": len(prog_ids),
                "map_count": len(map_ids)}

    # 4. ok / requires_root
    if not readable and user_ref_count == 0:
        return {"verdict": "requires_root",
                "reason": (
                    "/sys/fs/bpf mounted but not readable "
                    f"as this UID ; {pids_scanned} PID(s) "
                    "scanned via fdinfo showed no BPF "
                    "refs. Re-run as root for the full "
                    "inventory.")}

    return {"verdict": "ok_empty",
            "reason": (
                f"No BPF pins or refs visible — "
                f"{pids_scanned} PID(s) scanned, 0 BPF "
                "fdinfo refs.")}


def status(config: Optional[dict] = None,
           bpf_fs: str = DEFAULT_BPF_FS,
           mounts: str = DEFAULT_MOUNTS,
           proc_root: str = DEFAULT_PROC) -> dict:
    mounted = is_bpffs_mounted(mounts, bpf_fs)
    pin_count, readable = list_pins(bpf_fs)
    prog_ids, map_ids, pids_scanned = scan_user_fdinfo(
        proc_root)
    verdict = classify(mounted, pin_count, readable,
                        prog_ids, map_ids, pids_scanned)
    return {
        "ok": verdict["verdict"] not in (
            "unknown", "excessive_pins"),
        "bpffs_mounted": mounted,
        "pin_count": pin_count,
        "pin_readable": readable,
        "prog_id_count": len(prog_ids),
        "map_id_count": len(map_ids),
        "pids_scanned": pids_scanned,
        "verdict": verdict,
    }
