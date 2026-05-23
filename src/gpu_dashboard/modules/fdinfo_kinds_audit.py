"""Module fdinfo_kinds_audit — anon_inode FD classifier
(R&D #67.2).

Beyond regular files, sockets, and pipes, Linux processes hold a
zoo of "anonymous-inode" file descriptors :

  anon_inode:[eventfd]      lightweight cross-thread signaling
  anon_inode:[timerfd]      kernel timer delivered through poll()
  anon_inode:[signalfd]     signal delivered through poll()
  anon_inode:[eventpoll]    epoll instance
  anon_inode:[io_uring]     io_uring submission/completion rings
  anon_inode:[pidfd]        process handle (not in this audit's
                              alert set — informational)
  anon_inode:inotify        inotify watcher (covered separately
                              by inotify_audit, included here for
                              counts)
  anon_inode:sync_file      DMA-buf fence (graphics)

This audit walks /proc/*/fd/ readlinks, classifies anon_inode
targets, and reads /proc/*/fdinfo/* for the kinds whose
fdinfo lines reveal interesting state :

* eventpoll — `tfd:` lines count watched targets ; very large
  counts indicate a runaway watcher (real bugs in Electron-
  based apps, broken file watchers, fanotify mis-use).
* io_uring — running unprivileged processes with io_uring open
  is a real footgun (multiple privilege-escalation CVEs in the
  ring 2022-2024). On a homelab the user usually has a few :
  systemd-journald, fwupd, and rare apps. Knowing how many and
  whose helps decide if kernel.io_uring_disabled=2 is safe.

Verdicts (priority order) :
  io_uring_in_unprivileged_proc  ≥1 io_uring instance held by a
                                   process whose UID is not 0
                                   (security/footgun warning).
  epoll_watch_runaway              ≥1 eventpoll with ≥5 000
                                   tfd: lines.
  eventfd_leak                     A single PID holds ≥100
                                   eventfd FDs (likely a leak).
  requires_root                    Most /proc/<pid>/fdinfo
                                   entries are unreadable —
                                   running unprivileged so the
                                   sample is biased toward our
                                   own pid.
  ok                               Counts within normal range.
  unknown                          /proc absent (test).

stdlib only.
"""
from __future__ import annotations

import os
from typing import Dict, List, Optional


NAME = "fdinfo_kinds_audit"


_PROC = "/proc"

_KIND_PREFIX = "anon_inode:"
_ALERT_KINDS = ("[eventfd]", "[timerfd]", "[signalfd]",
                 "[eventpoll]", "[io_uring]", "[pidfd]",
                 "inotify", "sync_file")

_EPOLL_RUNAWAY = 5_000
_EVENTFD_LEAK_PER_PID = 100
_IO_URING_UID0_OK = True


def _readlink(path: str) -> Optional[str]:
    try:
        return os.readlink(path)
    except OSError:
        return None


def _read(path: str) -> Optional[str]:
    try:
        with open(path) as f:
            return f.read()
    except OSError:
        return None


def _proc_uid(pid: str, proc_root: str = _PROC) -> Optional[int]:
    text = _read(os.path.join(proc_root, pid, "status"))
    if not text:
        return None
    for line in text.splitlines():
        if line.startswith("Uid:"):
            parts = line.split()
            if len(parts) >= 2:
                try:
                    return int(parts[1])
                except ValueError:
                    return None
    return None


def list_pids(proc_root: str = _PROC) -> List[str]:
    try:
        return sorted(n for n in os.listdir(proc_root)
                          if n.isdigit())
    except OSError:
        return []


def kind_of(link_target: str) -> Optional[str]:
    if not link_target.startswith(_KIND_PREFIX):
        return None
    return link_target[len(_KIND_PREFIX):]


def epoll_watch_count(fdinfo_text: str) -> int:
    return sum(1 for ln in fdinfo_text.splitlines()
                  if ln.startswith("tfd:"))


def scan_pid(pid: str, proc_root: str = _PROC) -> dict:
    """Returns dict with :
      - kinds_count: {kind: int}
      - fdinfo_readable: int (count of fdinfo files we could read)
      - eventpoll_max_watch: int (max tfd: lines we saw)
      - io_uring_count: int
      - eventfd_count: int
      - uid: int or None
    """
    fd_dir = os.path.join(proc_root, pid, "fd")
    fdinfo_dir = os.path.join(proc_root, pid, "fdinfo")
    counts: Dict[str, int] = {}
    eventpoll_max = 0
    fdinfo_readable = 0
    try:
        entries = os.listdir(fd_dir)
    except OSError:
        return {"kinds_count": counts,
                  "fdinfo_readable": 0,
                  "eventpoll_max_watch": 0,
                  "io_uring_count": 0,
                  "eventfd_count": 0,
                  "uid": _proc_uid(pid, proc_root)}
    for f in entries:
        target = _readlink(os.path.join(fd_dir, f))
        if not target:
            continue
        kind = kind_of(target)
        if kind is None:
            continue
        counts[kind] = counts.get(kind, 0) + 1
        if kind == "[eventpoll]":
            txt = _read(os.path.join(fdinfo_dir, f))
            if txt is not None:
                fdinfo_readable += 1
                n = epoll_watch_count(txt)
                if n > eventpoll_max:
                    eventpoll_max = n
        elif kind == "[io_uring]":
            if _read(os.path.join(fdinfo_dir, f)) is not None:
                fdinfo_readable += 1
    return {"kinds_count": counts,
              "fdinfo_readable": fdinfo_readable,
              "eventpoll_max_watch": eventpoll_max,
              "io_uring_count": counts.get("[io_uring]", 0),
              "eventfd_count": counts.get("[eventfd]", 0),
              "uid": _proc_uid(pid, proc_root)}


def aggregate(scans: Dict[str, dict]) -> dict:
    all_kinds: Dict[str, int] = {}
    iouring_in_nonroot: List[str] = []
    eventfd_offenders: List[dict] = []
    epoll_offenders: List[dict] = []
    total_fdinfo_readable = 0
    pids_with_anon = 0
    for pid, scan in scans.items():
        for k, v in scan["kinds_count"].items():
            all_kinds[k] = all_kinds.get(k, 0) + v
        if scan["kinds_count"]:
            pids_with_anon += 1
        total_fdinfo_readable += scan["fdinfo_readable"]
        if (scan["io_uring_count"] > 0
                and scan["uid"] not in (0, None)):
            iouring_in_nonroot.append(pid)
        if scan["eventfd_count"] >= _EVENTFD_LEAK_PER_PID:
            eventfd_offenders.append(
                {"pid": pid, "count": scan["eventfd_count"]})
        if scan["eventpoll_max_watch"] >= _EPOLL_RUNAWAY:
            epoll_offenders.append(
                {"pid": pid,
                  "watches": scan["eventpoll_max_watch"]})
    return {"all_kinds": all_kinds,
              "pid_count": len(scans),
              "pids_with_anon": pids_with_anon,
              "fdinfo_readable": total_fdinfo_readable,
              "iouring_in_nonroot": iouring_in_nonroot,
              "eventfd_offenders": eventfd_offenders,
              "epoll_offenders": epoll_offenders}


def classify(agg: dict, proc_present: bool) -> dict:
    if not proc_present:
        return {"verdict": "unknown",
                "reason": "/proc absent.",
                "recommendation": ""}

    # 1) io_uring_in_unprivileged_proc
    bad = agg.get("iouring_in_nonroot", [])
    if bad:
        sample = ", ".join(bad[:5])
        return {"verdict": "io_uring_in_unprivileged_proc",
                "reason": (f"{len(bad)} non-root process(es) "
                          f"hold io_uring instances : "
                          f"{sample}. Unprivileged io_uring has "
                          f"a long CVE history."),
                "recommendation": _recipe_iouring_unprivileged()}

    # 2) epoll_watch_runaway
    big = agg.get("epoll_offenders", [])
    if big:
        sample = ", ".join(
            f"pid={e['pid']} watches={e['watches']}"
                for e in big[:3])
        return {"verdict": "epoll_watch_runaway",
                "reason": (f"{len(big)} eventpoll instance(s) "
                          f"with >= {_EPOLL_RUNAWAY} watched FDs"
                          f" : {sample}."),
                "recommendation": _recipe_epoll()}

    # 3) eventfd_leak
    leaks = agg.get("eventfd_offenders", [])
    if leaks:
        sample = ", ".join(
            f"pid={e['pid']}/{e['count']}"
                for e in leaks[:3])
        return {"verdict": "eventfd_leak",
                "reason": (f"{len(leaks)} process(es) hold "
                          f">= {_EVENTFD_LEAK_PER_PID} eventfd "
                          f"FDs : {sample}."),
                "recommendation": _recipe_eventfd_leak()}

    # 4) requires_root — many pids visible but barely any fdinfo
    pid_count = agg.get("pid_count", 0)
    fdinfo_readable = agg.get("fdinfo_readable", 0)
    if pid_count >= 30 and fdinfo_readable <= 5:
        return {"verdict": "requires_root",
                "reason": (f"Scanned {pid_count} PIDs but only "
                          f"{fdinfo_readable} fdinfo entries "
                          f"were readable — running as an "
                          f"unprivileged user."),
                "recommendation": _recipe_requires_root()}

    all_kinds = agg.get("all_kinds", {})
    summary = ", ".join(f"{k}={v}"
                              for k, v in sorted(
                                  all_kinds.items(),
                                  key=lambda kv: -kv[1])[:4])
    return {"verdict": "ok",
            "reason": (f"{pid_count} PIDs ; {summary or 'no '
                      f'anon_inode FDs'}."),
            "recommendation": ""}


def status(config=None, proc_root: str = _PROC) -> dict:
    proc_present = os.path.isdir(proc_root)
    scans: Dict[str, dict] = {}
    if proc_present:
        for pid in list_pids(proc_root):
            scans[pid] = scan_pid(pid, proc_root)
    agg = aggregate(scans)
    verdict = classify(agg, proc_present)

    return {"ok": proc_present,
              "pid_count": agg["pid_count"],
              "pids_with_anon": agg["pids_with_anon"],
              "fdinfo_readable": agg["fdinfo_readable"],
              "all_kinds": agg["all_kinds"],
              "iouring_in_nonroot": agg["iouring_in_nonroot"][:5],
              "eventfd_offenders": agg["eventfd_offenders"][:5],
              "epoll_offenders": agg["epoll_offenders"][:5],
              "verdict": verdict}


# ── recovery recipes ────────────────────────────────────────────

def _recipe_iouring_unprivileged() -> str:
    return ("# Unprivileged io_uring usage. To restrict :\n"
            "# kernel.io_uring_disabled = 2  (only root)\n"
            "# kernel.io_uring_disabled = 1  (group must match)\n"
            "echo 2 | sudo tee /proc/sys/kernel/io_uring_disabled\n"
            "# Persist in /etc/sysctl.d/99-io-uring.conf\n"
            "# Identify which app uses it :\n"
            "sudo ls -l /proc/*/fd/* 2>/dev/null \\\n"
            "  | grep io_uring\n")


def _recipe_epoll() -> str:
    return ("# Runaway eventpoll watcher. Identify it :\n"
            "for f in /proc/*/fdinfo/*; do\n"
            "  n=$(grep -c '^tfd:' \"$f\" 2>/dev/null)\n"
            "  [ \"${n:-0}\" -gt 1000 ] && echo \"$f $n\"\n"
            "done | sort -k2 -nr | head\n"
            "# Restart the offending app or raise the\n"
            "# /proc/sys/fs/epoll/max_user_watches limit.\n")


def _recipe_eventfd_leak() -> str:
    return ("# A process holds many eventfd FDs.\n"
            "# Cross-check the actual fd table :\n"
            "ls -l /proc/<pid>/fd | grep eventfd | wc -l\n"
            "# Increase nofile ulimit if legitimate, or restart\n"
            "# the offender to recover.\n")


def _recipe_requires_root() -> str:
    return ("# Only own-PID fdinfo readable. To audit system-wide :\n"
            "sudo systemctl edit gpu-dashboard.service\n"
            "# Or run a one-shot probe :\n"
            "sudo cat /proc/*/fdinfo/* 2>/dev/null | wc -l\n")
