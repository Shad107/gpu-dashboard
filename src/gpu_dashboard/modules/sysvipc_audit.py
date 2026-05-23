"""Module sysvipc_audit — SysV IPC leak detector (R&D #45.3).

Reads /proc/sysvipc/{shm, sem, msg} :

  shm   per-row : key, shmid, perms, size, cpid, lpid, nattch,
        uid, gid, cuid, cgid, atime, dtime, ctime, rss, swap
        — nattch == 0 + significant size = orphaned segment.
  sem   per-row : key, semid, perms, nsems, uid, gid, cuid,
        cgid, otime, ctime — high count = exhaustion risk
        on workloads that fork many short-lived semaphore
        users (postgres, older CUDA-MPS, certain Python
        multiprocessing patterns).
  msg   per-row : key, msqid, perms, cbytes, qnum, lspid,
        lrpid, uid, gid, cuid, cgid, stime, rtime, ctime
        — cbytes growing = unread queue accumulation.

Verdicts (priority-ordered) :
  stale_shm           ≥1 shm segment with nattch=0 + size ≥ 1 MB
                      + ctime older than 1 hour → orphaned, never
                      cleaned up (kernel keeps it until ipcrm or
                      reboot).
  sem_exhaustion      ≥ 80 % of SEMMNI default (32k) — risk of
                      ENOSPC on fork()/sem_init() under load.
  msg_queue_backlog   any msg queue with cbytes > 1 MB (unread
                      messages piling up).
  ok                  no orphans, sem count reasonable.
  no_sysvipc          /proc/sysvipc absent or empty (rare).
  unknown             /proc/sysvipc unreadable.

stdlib only.
"""
from __future__ import annotations

import os
import time
from typing import Optional


NAME = "sysvipc_audit"


_PROC_SYSVIPC = "/proc/sysvipc"


def _read(p: str) -> Optional[str]:
    try:
        with open(p) as f:
            return f.read()
    except OSError:
        return None


def parse_table(text: str) -> list:
    """Parse /proc/sysvipc/* file — first line is header, remaining
    rows are space-separated integers."""
    if not text:
        return []
    lines = text.splitlines()
    if len(lines) < 1:
        return []
    header = lines[0].split()
    if not header:
        return []
    out: list = []
    for line in lines[1:]:
        parts = line.split()
        if len(parts) != len(header):
            continue
        row: dict = {}
        ok = True
        for k, v in zip(header, parts):
            try:
                row[k] = int(v)
            except ValueError:
                ok = False
                break
        if ok:
            out.append(row)
    return out


def parse_shm(text: str) -> list:
    return parse_table(text)


def parse_sem(text: str) -> list:
    return parse_table(text)


def parse_msg(text: str) -> list:
    return parse_table(text)


_RECIPE_STALE_SHM = (
    "# Stale SysV shm segment(s) — nattch=0 means no process is\n"
    "# attached but the kernel keeps the memory until explicit\n"
    "# ipcrm or reboot. Inspect + clean :\n"
    "ipcs -m\n"
    "# Identify the shmid from the card, then :\n"
    "ipcrm -m <SHMID>\n"
    "# Common culprits : crashed postgres / CUDA-MPS / older\n"
    "# Python multiprocessing.shared_memory consumers."
)

_RECIPE_SEM_EXHAUST = (
    "# Semaphore set count approaching SEMMNI default (32k).\n"
    "# Risk of ENOSPC on next semget(). Bump :\n"
    "sudo tee /etc/sysctl.d/99-sem.conf <<'EOF'\n"
    "# kernel.sem = SEMMSL SEMMNS SEMOPM SEMMNI\n"
    "kernel.sem = 32000 1024000000 500 65536\n"
    "EOF\n"
    "sudo sysctl --system"
)

_RECIPE_MSG_BACKLOG = (
    "# A SysV message queue has > 1 MB of unread bytes — readers\n"
    "# may be dead/blocked. Inspect via :\n"
    "ipcs -q\n"
    "# Then ipcrm -q <MSQID> if you've confirmed nothing is\n"
    "# legitimately consuming."
)


_STALE_SIZE_MIN = 1 * 1024 * 1024     # 1 MB
_STALE_AGE_MIN = 3600                  # 1 hour
_SEM_THRESHOLD = 32_000 * 0.8
_MSG_BACKLOG_BYTES = 1 * 1024 * 1024


def classify(shm: list, sem: list, msg: list,
              now: Optional[int] = None) -> dict:
    if now is None:
        now = int(time.time())
    if not shm and not sem and not msg:
        # Could be missing OR genuinely empty — caller decides via
        # status() whether to use no_sysvipc vs ok.
        return {"verdict": "ok",
                "reason": "No SysV IPC objects in use.",
                "recommendation": ""}
    stale = [
        s for s in shm
        if s.get("nattch") == 0
        and s.get("size", 0) >= _STALE_SIZE_MIN
        and (now - s.get("ctime", now)) >= _STALE_AGE_MIN
    ]
    if stale:
        total = sum(s.get("size", 0) for s in stale)
        return {"verdict": "stale_shm",
                "reason": (f"{len(stale)} orphaned shm segment(s), "
                           f"total {total // (1024 * 1024)} MB. "
                           f"Kernel keeps them until ipcrm / reboot."),
                "recommendation": _RECIPE_STALE_SHM}
    if len(sem) >= _SEM_THRESHOLD:
        return {"verdict": "sem_exhaustion",
                "reason": (f"{len(sem)} semaphore sets — close to "
                           f"SEMMNI default 32k. Risk of ENOSPC."),
                "recommendation": _RECIPE_SEM_EXHAUST}
    backed = [m for m in msg
               if m.get("cbytes", 0) >= _MSG_BACKLOG_BYTES]
    if backed:
        return {"verdict": "msg_queue_backlog",
                "reason": (f"{len(backed)} msg queue(s) with > 1 MB "
                           f"unread. Readers may be dead/blocked."),
                "recommendation": _RECIPE_MSG_BACKLOG}
    total_shm = sum(s.get("size", 0) for s in shm)
    return {"verdict": "ok",
            "reason": (f"{len(shm)} shm ({total_shm // (1024 * 1024)} MB), "
                       f"{len(sem)} sem, {len(msg)} msg — no leak "
                       f"or saturation signal."),
            "recommendation": ""}


def status(cfg=None) -> dict:
    if not os.path.isdir(_PROC_SYSVIPC):
        return {
            "ok": False,
            "verdict": {"verdict": "unknown",
                         "reason": ("/proc/sysvipc unreadable."),
                         "recommendation": ""},
            "shm": [], "sem": [], "msg": [],
        }
    shm_text = _read(os.path.join(_PROC_SYSVIPC, "shm")) or ""
    sem_text = _read(os.path.join(_PROC_SYSVIPC, "sem")) or ""
    msg_text = _read(os.path.join(_PROC_SYSVIPC, "msg")) or ""
    shm = parse_shm(shm_text)
    sem = parse_sem(sem_text)
    msg = parse_msg(msg_text)
    verdict = classify(shm, sem, msg)
    return {
        "ok": True,
        "shm_count": len(shm),
        "sem_count": len(sem),
        "msg_count": len(msg),
        "shm_total_bytes": sum(s.get("size", 0) for s in shm),
        "top_shm": sorted(shm, key=lambda r: -(r.get("size") or 0))[:20],
        "verdict": verdict,
    }
