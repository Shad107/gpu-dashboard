"""Module proc_locks_contention_audit — /proc/locks blocked-
waiter + total-count audit (R&D #87.2).

Existing file_locks_audit (R&D #42.1) does inode → path
resolution for LLM-model contention. This audit owns a
different signal :

  * BLOCKED waiters — entries in /proc/locks prefixed with
    "->" are tasks waiting on a held lock. Stuck NFS
    clients, dovecot lockfile storms, postgres advisory-
    lock deadlocks surface here before the app logs
    scream.
  * Total lock count — > 200 file locks system-wide
    points to a long-running daemon (dovecot, postgres)
    leaking flock handles.

/proc/locks line format :
  <N>: TYPE   KIND      ACCESS  PID  MAJ:MIN:INODE  START  END
  <N>: ->     ...                                              (BLOCKED waiter)

Verdicts (worst first) :

  lock_blocked_long_chain    ≥3 BLOCKED entries — a hot
                             inode with multiple waiters,
                             often a deadlock signature.
  lock_blocked_any           ≥1 BLOCKED waiter in the
                             table.
  many_locks                 > 200 total locks (leak —
                             dovecot, postgres advisory
                             pattern).
  ok                         no waiters, count nominal.
  unknown                    /proc/locks unreadable.
"""
from __future__ import annotations

import os
import re
from typing import Optional

DEFAULT_LOCKS = "/proc/locks"
DEFAULT_PROC = "/proc"

# Thresholds
_BLOCKED_CHAIN_FLOOR = 3
_MANY_LOCKS_FLOOR = 200

# Entry lines look like  "1: POSIX..." or "1: -> POSIX..."
_LOCK_LINE_RE = re.compile(
    r"^\d+:\s+(?P<blocked>->)?")


def _read_text(path: str) -> Optional[str]:
    try:
        with open(path, "r", encoding="utf-8") as fh:
            return fh.read()
    except (OSError, PermissionError):
        return None


def parse_locks(text: str) -> tuple[int, int, list[str]]:
    """Returns (total, blocked_count, blocked_lines)."""
    total = 0
    blocked: list[str] = []
    for line in text.splitlines():
        m = _LOCK_LINE_RE.match(line)
        if m is None:
            continue
        total += 1
        if m.group("blocked"):
            blocked.append(line.strip())
    return (total, len(blocked), blocked)


def _extract_pids(blocked_lines: list[str]) -> set[int]:
    """Pulls PID column (5th whitespace token after the
    waiter marker) out of each blocked line."""
    pids: set[int] = set()
    for line in blocked_lines:
        toks = line.split()
        # toks: ['N:', '->', 'POSIX', 'ADVISORY', 'WRITE',
        #        '<pid>', 'major:minor:inode', start, end]
        if len(toks) < 6:
            continue
        try:
            pids.add(int(toks[5]))
        except ValueError:
            continue
    return pids


def _resolve_comms(proc_root: str,
                    pids: set[int]) -> dict[int, str]:
    out: dict[int, str] = {}
    for pid in pids:
        path = os.path.join(proc_root, str(pid), "comm")
        try:
            with open(path, "r", encoding="utf-8") as fh:
                out[pid] = fh.read().strip()
        except (OSError, PermissionError):
            out[pid] = ""
    return out


def classify(total: int, blocked_count: int,
             blocked_pids: set[int],
             comms: dict[int, str]) -> dict:
    if total == 0 and blocked_count == 0:
        return {"verdict": "unknown",
                "reason": "/proc/locks unreadable or empty."}

    if blocked_count >= _BLOCKED_CHAIN_FLOOR:
        proc_summary = ", ".join(
            f"{comms.get(p, '')}({p})"
            for p in sorted(blocked_pids)[:3])
        return {"verdict": "lock_blocked_long_chain",
                "reason": (
                    f"{blocked_count} BLOCKED waiter(s) "
                    f"across {len(blocked_pids)} PID(s) "
                    f"({proc_summary}) — likely deadlock "
                    "or hot-inode contention."),
                "blocked_count": blocked_count,
                "pid_count": len(blocked_pids)}

    if blocked_count > 0:
        proc_summary = ", ".join(
            f"{comms.get(p, '')}({p})"
            for p in sorted(blocked_pids)[:3])
        return {"verdict": "lock_blocked_any",
                "reason": (
                    f"{blocked_count} BLOCKED waiter(s) "
                    f"({proc_summary}) — a process is "
                    "stalled on a held lock."),
                "blocked_count": blocked_count}

    if total > _MANY_LOCKS_FLOOR:
        return {"verdict": "many_locks",
                "reason": (
                    f"{total} file locks held — daemon "
                    "leak signature (dovecot, postgres "
                    "advisory pattern)."),
                "total": total}

    return {"verdict": "ok",
            "reason": (
                f"{total} file lock(s), no BLOCKED "
                "waiters.")}


def status(config: Optional[dict] = None,
           locks_path: str = DEFAULT_LOCKS,
           proc_root: str = DEFAULT_PROC) -> dict:
    text = _read_text(locks_path)
    if text is None:
        return {
            "ok": False,
            "total": 0,
            "blocked": 0,
            "blocked_pids": [],
            "verdict": {
                "verdict": "unknown",
                "reason": "/proc/locks unreadable."},
        }
    total, blocked_count, blocked_lines = parse_locks(text)
    blocked_pids = _extract_pids(blocked_lines)
    comms = _resolve_comms(proc_root, blocked_pids)
    verdict = classify(total, blocked_count, blocked_pids,
                        comms)
    return {
        "ok": verdict["verdict"] not in (
            "lock_blocked_long_chain", "unknown"),
        "total": total,
        "blocked": blocked_count,
        "blocked_pids": [
            {"pid": p, "comm": comms.get(p, "")}
            for p in sorted(blocked_pids)],
        "verdict": verdict,
    }
