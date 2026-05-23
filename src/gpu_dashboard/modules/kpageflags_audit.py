"""Module kpageflags_audit — /proc/kpageflags physical-page
flag audit (R&D #69.2).

/proc/kpageflags is a binary file where each 8-byte entry maps
to one physical page-frame number (PFN). Each value is a
bit-vector of KPF_* flags exposing per-page state — uptodate,
dirty, LRU class, slab, hugepage, hwpoison, KSM, anonymous
mapping, transparent-hugepage, etc.

This is the ONLY place where the kernel surfaces a per-page
hardware-error / poison signal :
  KPF_HWPOISON (bit 19) — a page that ECC scrubbing or PCI AER
                          flagged as bad. Even a single one is
                          worth pulling DIMM out.
  KPF_UNEVICTABLE (bit 18) — pages permanently held in RAM
                          (mlocked / ramfs / GPU-pinned). A
                          large excess flags GPU pinned-memory
                          runaway.

Plus a coarse fragmentation signal :
  KPF_COMPOUND_HEAD (bit 15) / KPF_COMPOUND_TAIL (bit 16) ratio
  diverges from the expected (head:tail) pattern as transparent
  hugepages split under fragmentation pressure.

Reads :
  /proc/kpageflags                (0400 — root only)
  /proc/kpagecount                (companion, also root)
  /proc/sys/vm/block_dump          legacy I/O debug toggle
  /proc/sys/vm/unprivileged_userfaultfd
                                    informational; userfaultfd
                                    from unprivileged tasks
                                    expands the attack surface.

Verdicts (priority order) :
  excess_unevictable_or_hwpoison   ≥1 page with KPF_HWPOISON OR
                                     KPF_UNEVICTABLE ratio
                                     > 30 % of sampled pages.
  high_compound_fragmentation      COMPOUND_TAIL / COMPOUND_HEAD
                                     ratio < 100 (huge pages
                                     fragmenting).
  kpageflags_unreadable_no_capsys  file present but unreadable
                                     AND unprivileged_userfaultfd
                                     enabled — CAP_SYS_ADMIN
                                     needed for full audit.
  requires_root                    file present but unreadable
                                     (and userfaultfd is off).
  ok                               readable, flag histogram
                                     sane.
  unknown                          /proc/kpageflags absent.

stdlib only.
"""
from __future__ import annotations

import os
import struct
from typing import Dict, Optional


NAME = "kpageflags_audit"


_PROC_KPAGEFLAGS = "/proc/kpageflags"
_PROC_KPAGECOUNT = "/proc/kpagecount"
_PROC_BLOCK_DUMP = "/proc/sys/vm/block_dump"
_PROC_UFFD = "/proc/sys/vm/unprivileged_userfaultfd"


# KPF_* bit positions (uapi/linux/kernel-page-flags.h).
KPF_LOCKED = 0
KPF_ERROR = 1
KPF_REFERENCED = 2
KPF_UPTODATE = 3
KPF_DIRTY = 4
KPF_LRU = 5
KPF_ACTIVE = 6
KPF_SLAB = 7
KPF_WRITEBACK = 8
KPF_RECLAIM = 9
KPF_BUDDY = 10
KPF_MMAP = 11
KPF_ANON = 12
KPF_SWAPCACHE = 13
KPF_SWAPBACKED = 14
KPF_COMPOUND_HEAD = 15
KPF_COMPOUND_TAIL = 16
KPF_HUGE = 17
KPF_UNEVICTABLE = 18
KPF_HWPOISON = 19
KPF_NOPAGE = 20
KPF_KSM = 21
KPF_THP = 22


_FLAG_NAMES = {
    KPF_LOCKED: "LOCKED", KPF_ERROR: "ERROR",
    KPF_REFERENCED: "REFERENCED", KPF_UPTODATE: "UPTODATE",
    KPF_DIRTY: "DIRTY", KPF_LRU: "LRU", KPF_ACTIVE: "ACTIVE",
    KPF_SLAB: "SLAB", KPF_WRITEBACK: "WRITEBACK",
    KPF_RECLAIM: "RECLAIM", KPF_BUDDY: "BUDDY",
    KPF_MMAP: "MMAP", KPF_ANON: "ANON",
    KPF_SWAPCACHE: "SWAPCACHE", KPF_SWAPBACKED: "SWAPBACKED",
    KPF_COMPOUND_HEAD: "COMPOUND_HEAD",
    KPF_COMPOUND_TAIL: "COMPOUND_TAIL", KPF_HUGE: "HUGE",
    KPF_UNEVICTABLE: "UNEVICTABLE", KPF_HWPOISON: "HWPOISON",
    KPF_NOPAGE: "NOPAGE", KPF_KSM: "KSM", KPF_THP: "THP",
}


_UNEVICTABLE_RATIO_THRESHOLD = 0.30
_COMPOUND_TAIL_PER_HEAD_FLOOR = 100   # below = bad
_MAX_PAGES_SAMPLED = 1 << 18           # 262 144 pages = 1 GiB


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


def scan_kpageflags(path: str = _PROC_KPAGEFLAGS,
                       limit: int = _MAX_PAGES_SAMPLED
                       ) -> dict:
    """Returns {present, readable, pages_sampled, flag_counts}.

    Each PFN entry is 8 bytes little-endian unsigned."""
    out = {"present": False, "readable": False,
              "pages_sampled": 0,
              "flag_counts": {}}
    if not os.path.exists(path):
        return out
    out["present"] = True
    try:
        with open(path, "rb") as f:
            data = f.read(limit * 8)
    except PermissionError:
        out["readable"] = False
        return out
    except OSError:
        out["readable"] = False
        return out
    out["readable"] = True
    counts: Dict[str, int] = {n: 0 for n in _FLAG_NAMES.values()}
    n_pages = len(data) // 8
    out["pages_sampled"] = n_pages
    for i in range(0, n_pages * 8, 8):
        try:
            (val,) = struct.unpack_from("<Q", data, i)
        except struct.error:
            break
        for bit, name in _FLAG_NAMES.items():
            if val & (1 << bit):
                counts[name] += 1
    # Strip zero-count entries for output sanity.
    out["flag_counts"] = {k: v for k, v in counts.items()
                                  if v > 0}
    return out


def classify(scan: dict,
              uffd_unprivileged: Optional[int]) -> dict:
    if not scan["present"]:
        return {"verdict": "unknown",
                "reason": ("/proc/kpageflags absent — kernel "
                          "built without CONFIG_PROC_PAGE_MONITOR."),
                "recommendation": ""}

    if not scan["readable"]:
        if uffd_unprivileged == 1:
            return {"verdict":
                        "kpageflags_unreadable_no_capsys",
                    "reason": ("/proc/kpageflags is 0400 ; "
                              "unprivileged_userfaultfd is "
                              "enabled — CAP_SYS_ADMIN required "
                              "for full per-page audit."),
                    "recommendation":
                        _recipe_kpf_unreadable_uffd()}
        return {"verdict": "requires_root",
                "reason": ("/proc/kpageflags is 0400 root-only ; "
                          "running as unprivileged user."),
                "recommendation": _recipe_requires_root()}

    counts = scan["flag_counts"]
    n = scan["pages_sampled"] or 1

    # 1) excess_unevictable_or_hwpoison
    hwpoison = counts.get("HWPOISON", 0)
    unevictable = counts.get("UNEVICTABLE", 0)
    if (hwpoison > 0
            or (unevictable / n) > _UNEVICTABLE_RATIO_THRESHOLD):
        return {"verdict": "excess_unevictable_or_hwpoison",
                "reason": (f"HWPOISON={hwpoison} "
                          f"UNEVICTABLE={unevictable} of "
                          f"{n} sampled pages "
                          f"({100*unevictable/n:.1f} % "
                          f"unevictable)."),
                "recommendation":
                    _recipe_hwpoison_unevictable()}

    # 2) high_compound_fragmentation
    head = counts.get("COMPOUND_HEAD", 0)
    tail = counts.get("COMPOUND_TAIL", 0)
    if head >= 5 and tail < head * _COMPOUND_TAIL_PER_HEAD_FLOOR:
        ratio = tail / head if head else 0
        return {"verdict": "high_compound_fragmentation",
                "reason": (f"COMPOUND_HEAD={head}, "
                          f"COMPOUND_TAIL={tail} (ratio "
                          f"{ratio:.1f} tail/head ; expected "
                          f">= 100). THP under fragmentation "
                          f"pressure."),
                "recommendation": _recipe_compound_frag()}

    return {"verdict": "ok",
            "reason": (f"Sampled {n} pages ; HWPOISON=0 ; "
                      f"unevictable share "
                      f"{100*unevictable/n:.1f} % ; "
                      f"compound head/tail "
                      f"{head}/{tail}."),
            "recommendation": ""}


def status(config=None,
            proc_kpageflags: str = _PROC_KPAGEFLAGS,
            proc_kpagecount: str = _PROC_KPAGECOUNT,
            proc_block_dump: str = _PROC_BLOCK_DUMP,
            proc_uffd: str = _PROC_UFFD,
            sample_limit: int = _MAX_PAGES_SAMPLED) -> dict:
    scan = scan_kpageflags(proc_kpageflags, sample_limit)
    uffd_unprivileged = _read_int(proc_uffd)
    block_dump = _read_int(proc_block_dump)
    verdict = classify(scan, uffd_unprivileged)
    return {"ok": scan["present"],
              "present": scan["present"],
              "readable": scan["readable"],
              "pages_sampled": scan["pages_sampled"],
              "flag_counts": scan["flag_counts"],
              "unprivileged_userfaultfd": uffd_unprivileged,
              "block_dump": block_dump,
              "verdict": verdict}


# ── recovery recipes ────────────────────────────────────────────

def _recipe_hwpoison_unevictable() -> str:
    return ("# Per-page HWPOISON / large unevictable working set.\n"
            "# Decode the affected page count :\n"
            "sudo grep -i 'hwpoison\\|memory' /proc/meminfo\n"
            "# Inspect dmesg for ECC and memory-failure events :\n"
            "sudo dmesg | grep -iE 'mce|memory failure|hwpoison'\n"
            "# Heavy unevictable usage is often mlocked or pinned\n"
            "# pages — find culprit via /proc/<pid>/status (VmLck).\n")


def _recipe_compound_frag() -> str:
    return ("# Compound (transparent hugepage) fragmentation.\n"
            "# Force compaction :\n"
            "echo 1 | sudo tee /proc/sys/vm/compact_memory\n"
            "# Check available hugepage orders :\n"
            "cat /sys/kernel/mm/transparent_hugepage/enabled\n"
            "cat /proc/buddyinfo\n")


def _recipe_kpf_unreadable_uffd() -> str:
    return ("# kpageflags needs CAP_SYS_ADMIN — userfaultfd is\n"
            "# already unprivileged so user has SOME per-page\n"
            "# access. To audit fully :\n"
            "sudo cat /proc/kpageflags | wc -c\n")


def _recipe_requires_root() -> str:
    return ("# /proc/kpageflags is 0400 root-only.\n"
            "sudo head -c 8 /proc/kpageflags | xxd\n"
            "# Each 8 bytes = one PFN's flag bitmap.\n")
