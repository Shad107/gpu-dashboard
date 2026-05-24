"""Module nfsd_stats_audit — Linux NFS server thread-pool +
duplicate-reply-cache health (R&D #83.3).

Homelab NAS bottleneck no dashboard surfaces — thread-pool
starvation and DRC (Duplicate Reply Cache) overflow silently
kill NFS write throughput.  Detects :

  * threads pinned 100 % — pool_stats shows threads-woken
    matching sockets-enqueued (every wake was already
    deferred).
  * thread count too low for the CPU count (Ubuntu default
    is 8 ; on a 12-core homelab that's a hot bottleneck).
  * reply cache miss rate > 10 % — the kernel is dropping
    duplicate requests out of the cache faster than they
    arrive, leading to repeated work and client retries.

Reads :

  /proc/fs/nfsd/threads            single integer
  /proc/fs/nfsd/pool_stats         per-pool packets /
                                   sockets-enqueued /
                                   threads-woken /
                                   threads-timedout
  /proc/fs/nfsd/reply_cache_stats  cache hits, misses,
                                   not cached, max entries
  /proc/cpuinfo                    for CPU count comparison

Verdicts (worst first) :

  reply_cache_overflow   not-cached ratio > 10 % of total
                         operations — DRC is hemorrhaging.
  threads_starved        in any pool, threads-woken /
                         sockets-enqueued > 95 % AND
                         sockets-enqueued > 1000 — wake
                         queue is always backed up.
  thread_count_low       nfsd threads < cpu_count / 2 with
                         cpu_count >= 8 — homelab CPU
                         starved.
  ok                     thread pool healthy.
  n/a                    nfsd kernel module not loaded.
"""
from __future__ import annotations

import os
import re
from typing import Optional

DEFAULT_NFSD_ROOT = "/proc/fs/nfsd"
DEFAULT_CPUINFO = "/proc/cpuinfo"

# Thresholds
_OVERFLOW_RATIO = 0.10        # 10 % not-cached
_STARVATION_RATIO = 0.95
_STARVATION_FLOOR = 1000
_LOW_THREAD_CPU_FLOOR = 8


def _read_text(path: str) -> Optional[str]:
    try:
        with open(path, "r", encoding="utf-8") as fh:
            return fh.read()
    except (OSError, PermissionError):
        return None


def _read_int(path: str) -> Optional[int]:
    s = _read_text(path)
    if s is None:
        return None
    try:
        return int(s.strip())
    except ValueError:
        return None


def is_nfsd_present(root: str = DEFAULT_NFSD_ROOT) -> bool:
    return os.path.isdir(root)


def parse_pool_stats(text: str) -> list[dict]:
    """Parses /proc/fs/nfsd/pool_stats.

    Format (kernel) :
      # pool packets-arrived sockets-enqueued threads-woken
                                                  threads-timedout
      0 1234 56 78 0
    """
    out: list[dict] = []
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        toks = line.split()
        if len(toks) < 5:
            continue
        try:
            out.append({
                "pool": int(toks[0]),
                "packets": int(toks[1]),
                "sockets_enqueued": int(toks[2]),
                "threads_woken": int(toks[3]),
                "threads_timedout": int(toks[4]),
            })
        except ValueError:
            continue
    return out


_DRC_RE = re.compile(
    r"^([\w ]+):\s+(\d+)(?:\s+\w+)?\s*$")


def parse_reply_cache(text: str) -> dict:
    """Parses /proc/fs/nfsd/reply_cache_stats key:value lines."""
    out: dict = {}
    for line in text.splitlines():
        m = _DRC_RE.match(line.strip())
        if m is None:
            continue
        key = m.group(1).strip().lower().replace(" ", "_")
        try:
            out[key] = int(m.group(2))
        except ValueError:
            continue
    return out


def count_cpus(cpuinfo_text: str) -> int:
    return sum(
        1 for ln in cpuinfo_text.splitlines()
        if ln.startswith("processor"))


def classify(state: dict) -> dict:
    if not state.get("nfsd_present"):
        return {"verdict": "n/a",
                "reason": (
                    "nfsd kernel module not loaded ; "
                    "/proc/fs/nfsd absent. Not running an "
                    "NFS server.")}

    drc = state.get("reply_cache") or {}
    pools = state.get("pools") or []
    threads = state.get("threads")
    cpu_count = state.get("cpu_count") or 0

    # 1. err — DRC overflow (not-cached ratio high)
    hits = drc.get("cache_hits", 0)
    misses = drc.get("cache_misses", 0)
    not_cached = drc.get("not_cached", 0)
    total = hits + misses + not_cached
    if total > 0 and not_cached / total > _OVERFLOW_RATIO:
        return {"verdict": "reply_cache_overflow",
                "reason": (
                    f"DRC not-cached = {not_cached} of "
                    f"{total} ops "
                    f"({not_cached/total:.0%}) — duplicate "
                    "reply cache is overflowing."),
                "not_cached_ratio": not_cached / total,
                "not_cached": not_cached}

    # 2. warn — threads starved (woke rate ~ enqueue rate)
    for p in pools:
        se = p["sockets_enqueued"]
        tw = p["threads_woken"]
        if (se >= _STARVATION_FLOOR
                and tw / se > _STARVATION_RATIO):
            return {"verdict": "threads_starved",
                    "reason": (
                        f"Pool {p['pool']} threads-woken "
                        f"= {tw} of {se} sockets-enqueued "
                        f"({tw/se:.0%}) — wake queue "
                        "always backed up."),
                    "pool": p["pool"],
                    "starve_ratio": tw / se}

    # 3. accent — thread count too low for CPU count
    if (threads is not None
            and cpu_count >= _LOW_THREAD_CPU_FLOOR
            and threads < cpu_count / 2):
        return {"verdict": "thread_count_low",
                "reason": (
                    f"nfsd thread count {threads} < "
                    f"cpu_count/2 ({cpu_count}/2). Raise "
                    "via /etc/nfs.conf [nfsd] threads=N."),
                "threads": threads,
                "cpu_count": cpu_count}

    thr_repr = "no daemon" if threads in (None, 0) else f"{threads}"
    return {"verdict": "ok",
            "reason": (
                f"nfsd healthy ; threads={thr_repr}, "
                f"{len(pools)} pool(s), DRC ratio "
                f"{(not_cached/total if total else 0):.1%}.")}


def status(config: Optional[dict] = None,
           root: str = DEFAULT_NFSD_ROOT,
           cpuinfo_path: str = DEFAULT_CPUINFO) -> dict:
    present = is_nfsd_present(root)
    threads = (
        _read_int(os.path.join(root, "threads"))
        if present else None)
    pool_text = (
        _read_text(os.path.join(root, "pool_stats"))
        if present else None)
    drc_text = (
        _read_text(os.path.join(root, "reply_cache_stats"))
        if present else None)
    cpuinfo = _read_text(cpuinfo_path) or ""
    cpu_count = count_cpus(cpuinfo)

    state = {
        "nfsd_present": present,
        "threads": threads,
        "pools": parse_pool_stats(pool_text or ""),
        "reply_cache": parse_reply_cache(drc_text or ""),
        "cpu_count": cpu_count,
    }
    verdict = classify(state)
    return {
        "ok": verdict["verdict"] not in (
            "reply_cache_overflow",),
        "nfsd_present": present,
        "threads": threads,
        "pool_count": len(state["pools"]),
        "cpu_count": cpu_count,
        "reply_cache": state["reply_cache"],
        "verdict": verdict,
    }
