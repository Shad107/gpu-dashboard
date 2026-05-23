"""Module vfs_limits_audit — VFS + io_uring headroom (R&D #46.3).

Reads the kernel-global filesystem ceilings :

  /proc/sys/fs/file-nr        "alloc 0 max" (current allocated /
                              free / max FDs system-wide).
  /proc/sys/fs/file-max       absolute max (often ~LONG_MAX on
                              modern kernels — effectively unlimited).
  /proc/sys/fs/nr_open        per-process FD limit (RLIMIT_NOFILE
                              hard ceiling).
  /proc/sys/fs/aio-nr         active POSIX aio contexts.
  /proc/sys/fs/aio-max-nr     max aio contexts. io_uring is *not*
                              counted here but shares the underlying
                              memcg/cgroup quota.
  /proc/sys/fs/pipe-max-size  default pipe capacity bytes.
  /proc/sys/fs/pipe-user-pages-{soft,hard}
                              per-user pipe page caps.

Verdicts (priority-ordered) :
  file_nr_high            allocated FDs > 80 % of file-max ceiling
                          (system-wide FD pressure ; rare on modern
                          kernels with LONG_MAX file-max).
  aio_nr_high             aio_nr > 80 % of aio_max_nr — workloads
                          like postgres + older Python aio that
                          create many ioctx may run out.
  ok                      headroom available.
  unknown                 /proc/sys/fs unreadable.

stdlib only.
"""
from __future__ import annotations

import os
from typing import Optional


NAME = "vfs_limits_audit"


_PROC_SYS_FS = "/proc/sys/fs"


def _read(p: str) -> Optional[str]:
    try:
        with open(p) as f:
            return f.read()
    except OSError:
        return None


def _read_int(p: str) -> Optional[int]:
    t = _read(p)
    if t is None:
        return None
    try:
        return int(t.strip())
    except ValueError:
        return None


def parse_file_nr(text: Optional[str]) -> dict:
    """/proc/sys/fs/file-nr → 'alloc 0 max' (3 tab-separated ints)."""
    if not text:
        return {}
    parts = text.split()
    if len(parts) < 3:
        return {}
    try:
        return {"allocated": int(parts[0]),
                  "free": int(parts[1]),
                  "max": int(parts[2])}
    except ValueError:
        return {}


_INT_FIELDS = (
    "file-max", "nr_open", "aio-nr", "aio-max-nr",
    "pipe-max-size", "pipe-user-pages-soft",
    "pipe-user-pages-hard",
)


def read_limits(sys_fs: str = _PROC_SYS_FS) -> dict:
    out: dict = {}
    file_nr_text = _read(os.path.join(sys_fs, "file-nr"))
    fn = parse_file_nr(file_nr_text)
    if fn:
        out["file_nr"] = fn
    for f in _INT_FIELDS:
        v = _read_int(os.path.join(sys_fs, f))
        if v is not None:
            out[f.replace("-", "_")] = v
    return out


_THRESHOLD = 0.80


_RECIPE_FILE_NR = (
    "# FD allocation > 80 % of file-max ceiling. Inspect who's\n"
    "# holding FDs :\n"
    "for p in /proc/[0-9]*; do\n"
    "  n=$(ls $p/fd 2>/dev/null | wc -l)\n"
    "  [ \"$n\" -gt 500 ] && echo \"$p $(cat $p/comm) fds=$n\"\n"
    "done | sort -k3 -t= -n | tail -10\n"
    "# Then bump :\n"
    "echo 2097152 | sudo tee /proc/sys/fs/file-max"
)

_RECIPE_AIO = (
    "# POSIX AIO context count approaching aio-max-nr — bump :\n"
    "echo 1048576 | sudo tee /proc/sys/fs/aio-max-nr\n"
    "echo 'fs.aio-max-nr = 1048576' | \\\n"
    "  sudo tee /etc/sysctl.d/99-aio.conf"
)


def classify(limits: dict) -> dict:
    if not limits:
        return {"verdict": "unknown",
                "reason": "/proc/sys/fs unreadable.",
                "recommendation": ""}
    fn = limits.get("file_nr") or {}
    alloc = fn.get("allocated", 0)
    fmax = fn.get("max", 0) or limits.get("file_max", 0)
    if fmax > 0 and alloc / fmax >= _THRESHOLD:
        return {"verdict": "file_nr_high",
                "reason": (f"file-nr allocated={alloc} of "
                           f"max={fmax} ({alloc / fmax * 100:.0f} %)."),
                "recommendation": _RECIPE_FILE_NR}
    aio_nr = limits.get("aio_nr", 0)
    aio_max = limits.get("aio_max_nr", 0)
    if aio_max > 0 and aio_nr / aio_max >= _THRESHOLD:
        return {"verdict": "aio_nr_high",
                "reason": (f"aio-nr={aio_nr} of aio-max-nr={aio_max} "
                           f"({aio_nr / aio_max * 100:.0f} %)."),
                "recommendation": _RECIPE_AIO}
    return {"verdict": "ok",
            "reason": (f"file-nr {alloc}/{fmax}, "
                       f"aio {aio_nr}/{aio_max}, "
                       f"nr_open={limits.get('nr_open')} — "
                       f"comfortable headroom."),
            "recommendation": ""}


def status(cfg=None) -> dict:
    limits = read_limits(_PROC_SYS_FS)
    verdict = classify(limits)
    return {
        "ok": bool(limits),
        "limits": limits,
        "verdict": verdict,
    }
