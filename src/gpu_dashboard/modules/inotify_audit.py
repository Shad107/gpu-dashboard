"""Module inotify_audit — inotify/fanotify watch-descriptor auditor (R&D #41.4).

inotify-based filesystem watchers (Plex / Jellyfin scanning library
directories, Syncthing watching sync folders, VS Code watching the
project tree, the gnome / kde file-managers, GitHub Copilot) all
consume two limits from /proc/sys/fs/inotify/* :

  max_user_watches      total inotify *watches* (one per watched
                        path) per UID. Default 8192 on older
                        distros, 65536 on modern ones, 524288
                        common on Arch / Fedora. A media library
                        with 50k files trivially busts the older
                        defaults.
  max_user_instances    total inotify *instances* (= inotify fds)
                        per UID. Default 128. Each watcher
                        process holds 1-N instances.
  max_queued_events     per-instance event queue depth (default
                        16384). Overflows manifest as the watcher
                        going silent until reopened.

Per-process /proc/<pid>/fdinfo/<n> exposes one `inotify wd:N ...`
line per watch (and `fanotify ino:... ...` for fanotify
descriptors) — counting these is a stdlib-friendly way to find
"which process is eating my watches".

Verdicts :
  approaching_max_watches    sum(per-pid wd) > 80 % of max_user_watches
                             for any UID — exhaustion imminent.
  instance_per_pid_high      ≥1 UID has > 80 % of max_user_instances.
  ok                         room to spare.
  no_watches_in_use          inotify is available but no watcher
                             processes — fine, just informational.
  unknown                    /proc/sys/fs/inotify unreadable.

stdlib only.
"""
from __future__ import annotations

import os
from typing import Optional


NAME = "inotify_audit"

# Hardening #13 originally marked this module EXPECTED_SLOW because
# walking /proc/<pid>/fdinfo/* costs ~500 ms. Hardening #15.2
# moved the walk into the shared `_proc_fd_cache`, so this module's
# measured cost dropped to ~48 ms on a warm cache and ~478 ms on
# cold cache — both at or under the 500 ms per-module budget. The
# EXPECTED_SLOW marker is no longer needed.


_PROC_SYS_INOTIFY = "/proc/sys/fs/inotify"
_PROC = "/proc"


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


def read_limits(sys_in: str = _PROC_SYS_INOTIFY) -> dict:
    out: dict = {}
    for f in ("max_user_watches", "max_user_instances",
              "max_queued_events"):
        v = _read_int(os.path.join(sys_in, f))
        if v is not None:
            out[f] = v
    return out


def count_inotify_fd(fdinfo_text: str) -> dict:
    """Parse one /proc/<pid>/fdinfo/<n> file content.

    Returns {"kind": "inotify"|"fanotify"|None, "watches": N}
    where watches is the count of wd-bearing lines for inotify, or
    fanotify-mark lines for fanotify.
    """
    if not fdinfo_text:
        return {"kind": None, "watches": 0}
    inotify_n = 0
    fanotify_n = 0
    for line in fdinfo_text.splitlines():
        s = line.lstrip()
        if s.startswith("inotify wd:"):
            inotify_n += 1
        elif s.startswith("fanotify ino:") or s.startswith("fanotify mnt_id:"):
            fanotify_n += 1
    if inotify_n > 0:
        return {"kind": "inotify", "watches": inotify_n}
    if fanotify_n > 0:
        return {"kind": "fanotify", "watches": fanotify_n}
    return {"kind": None, "watches": 0}


def _read_uid(status_text: str) -> Optional[int]:
    for line in status_text.splitlines():
        if line.startswith("Uid:"):
            parts = line.split()
            if len(parts) >= 2:
                try:
                    return int(parts[1])
                except ValueError:
                    return None
    return None


def scan_processes(proc_root: str = _PROC) -> list:
    """Walk /proc/*/fdinfo/* — best-effort, skips unreadable PIDs.

    Hardening #15: fdinfo walks delegate to `_proc_fd_cache`,
    shared with three other modules. /proc/<pid>/{comm,status}
    are still read directly per PID — they are not in the cache
    because no other module needs them.
    """
    from . import _proc_fd_cache
    out: list = []
    snapshot = _proc_fd_cache.scan_proc_fd(proc_root)
    for n, entry in snapshot.items():
        if not entry["fdinfo"]:
            # No readable fdinfo for this PID — match the prior
            # behavior of skipping it (the unreadable case fell
            # through the `os.listdir(fdinfo_dir) → OSError`
            # path).
            continue
        pid = entry["pid"]
        comm_text = _read(os.path.join(proc_root, n, "comm")) or ""
        comm = comm_text.strip()
        status_text = _read(os.path.join(proc_root, n, "status")) or ""
        uid = _read_uid(status_text)
        inotify_instances = 0
        inotify_watches = 0
        fanotify_instances = 0
        fanotify_watches = 0
        for text in entry["fdinfo"].values():
            parsed = count_inotify_fd(text or "")
            if parsed["kind"] == "inotify":
                inotify_instances += 1
                inotify_watches += parsed["watches"]
            elif parsed["kind"] == "fanotify":
                fanotify_instances += 1
                fanotify_watches += parsed["watches"]
        if (inotify_instances or inotify_watches
                or fanotify_instances or fanotify_watches):
            out.append({
                "pid": pid,
                "comm": comm,
                "uid": uid,
                "inotify_instances": inotify_instances,
                "inotify_watches": inotify_watches,
                "fanotify_instances": fanotify_instances,
                "fanotify_watches": fanotify_watches,
            })
    return out


def aggregate_by_uid(procs: list) -> dict:
    out: dict = {}
    for p in procs:
        uid = p.get("uid")
        if uid is None:
            continue
        agg = out.setdefault(uid, {"watches": 0, "instances": 0,
                                       "fanotify_watches": 0,
                                       "fanotify_instances": 0,
                                       "procs": 0})
        agg["watches"] += p.get("inotify_watches", 0)
        agg["instances"] += p.get("inotify_instances", 0)
        agg["fanotify_watches"] += p.get("fanotify_watches", 0)
        agg["fanotify_instances"] += p.get("fanotify_instances", 0)
        agg["procs"] += 1
    return out


_THRESHOLD_PCT = 0.80


_RECIPE_RAISE_WATCHES = (
    "# One UID is approaching max_user_watches — raise the limit.\n"
    "# A 524288 cap (≈ 2× current most-modern default) covers any\n"
    "# realistic media-library / project-tree scenario :\n"
    "echo 524288 | sudo tee /proc/sys/fs/inotify/max_user_watches\n"
    "# Persistent (interacts with shipped sysctl_d_audit) :\n"
    "echo 'fs.inotify.max_user_watches = 524288' | \\\n"
    "  sudo tee /etc/sysctl.d/99-inotify-watches.conf\n"
    "sudo sysctl --system"
)

_RECIPE_RAISE_INSTANCES = (
    "# One UID is approaching max_user_instances. The default 128\n"
    "# is fine for most desktops but tight if you run many\n"
    "# concurrent IDEs / watchers. Bump to 512 :\n"
    "echo 512 | sudo tee /proc/sys/fs/inotify/max_user_instances\n"
    "echo 'fs.inotify.max_user_instances = 512' | \\\n"
    "  sudo tee /etc/sysctl.d/99-inotify-instances.conf"
)


def classify(limits: dict, procs: list) -> dict:
    if not limits:
        return {"verdict": "unknown",
                "reason": "/proc/sys/fs/inotify unreadable.",
                "recommendation": ""}
    if not procs:
        return {"verdict": "no_watches_in_use",
                "reason": ("inotify is available but no watcher "
                           "processes detected on this host."),
                "recommendation": ""}
    by_uid = aggregate_by_uid(procs)
    max_watches = limits.get("max_user_watches") or 0
    max_instances = limits.get("max_user_instances") or 0
    # Watches first — exhaustion of this limit silently breaks
    # filesystem watching for the entire UID.
    worst_uid = None
    worst_watch_ratio = 0.0
    for uid, agg in by_uid.items():
        if max_watches > 0:
            ratio = agg["watches"] / max_watches
            if ratio > worst_watch_ratio:
                worst_watch_ratio = ratio
                worst_uid = uid
    if worst_watch_ratio >= _THRESHOLD_PCT:
        agg = by_uid[worst_uid]
        return {"verdict": "approaching_max_watches",
                "reason": (f"UID {worst_uid} holds "
                           f"{agg['watches']} inotify watches — "
                           f"{worst_watch_ratio:.0%} of "
                           f"max_user_watches={max_watches}. "
                           f"Filesystem watchers will start "
                           f"failing once the cap is hit."),
                "recommendation": _RECIPE_RAISE_WATCHES}
    worst_inst_uid = None
    worst_inst_ratio = 0.0
    for uid, agg in by_uid.items():
        if max_instances > 0:
            ratio = agg["instances"] / max_instances
            if ratio > worst_inst_ratio:
                worst_inst_ratio = ratio
                worst_inst_uid = uid
    if worst_inst_ratio >= _THRESHOLD_PCT:
        agg = by_uid[worst_inst_uid]
        return {"verdict": "instance_per_pid_high",
                "reason": (f"UID {worst_inst_uid} holds "
                           f"{agg['instances']} inotify instances "
                           f"across {agg['procs']} process(es) — "
                           f"{worst_inst_ratio:.0%} of "
                           f"max_user_instances={max_instances}."),
                "recommendation": _RECIPE_RAISE_INSTANCES}
    return {"verdict": "ok",
            "reason": (f"{len(procs)} watcher process(es) across "
                       f"{len(by_uid)} UID(s) ; worst "
                       f"watch-utilisation "
                       f"{worst_watch_ratio:.1%} of "
                       f"max_user_watches={max_watches}."),
            "recommendation": ""}


def status(cfg=None) -> dict:
    limits = read_limits(_PROC_SYS_INOTIFY)
    procs = scan_processes(_PROC)
    by_uid = aggregate_by_uid(procs)
    # Sort top-watchers (descending) for the UI.
    top = sorted(procs,
                  key=lambda p: p.get("inotify_watches", 0),
                  reverse=True)[:20]
    verdict = classify(limits, procs)
    return {
        "ok": bool(limits),
        "limits": limits,
        "process_count": len(procs),
        "by_uid": {str(k): v for k, v in by_uid.items()},
        "top_processes": top,
        "verdict": verdict,
    }
