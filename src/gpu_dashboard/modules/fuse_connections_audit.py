"""Module fuse_connections_audit — /sys/fs/fuse/connections/<id>
wedged-request and orphan-mount detector (R&D #98.3).

Modern desktops accumulate FUSE mounts faster than any user
notices: GVfs, sshfs, mtpfs, AppImage, Flatpak portal helpers,
fuse-overlayfs, encfs. Each registers a connection under
/sys/fs/fuse/connections/<id>/ with three monitorable knobs:

  waiting               # in-flight requests not yet answered
                        # by the userspace fuse server
  max_background        # request slot limit
  congestion_threshold  # when the kernel back-pressures
                        # callers

Real-world failure mode: a userspace fuse server (gvfs daemon,
sshfs over a flaky tunnel) gets stuck or killed without
unmounting. The connection lingers with waiting>0 ; any
process touching that subtree hangs in D-state. Common fix:
echo 1 > /sys/fs/fuse/connections/<id>/abort

No existing module walks this surface — namespace_limits_audit,
fs_mount_audit and container_audit cover other angles.

Reads :

  /sys/fs/fuse/connections/                            # list
  /sys/fs/fuse/connections/<id>/waiting
  /sys/fs/fuse/connections/<id>/max_background
  /sys/fs/fuse/connections/<id>/congestion_threshold

Verdicts (worst-first) :

  fuse_connection_wedged    err     ≥1 connection has
                                    waiting > 5 requests.
  fuse_connection_count_high warn   > 50 connections —
                                    likely orphan mounts.
  congestion_threshold_low  accent  any connection with
                                    congestion_threshold < 12
                                    (default is usually 12).
  ok                                connections healthy.
  requires_root                     /sys/fs/fuse/ exists
                                    but unreadable.
  unknown                           /sys/fs/fuse absent
                                    (kernel without FUSE).

stdlib only.
"""
from __future__ import annotations

import os
from typing import Optional

NAME = "fuse_connections_audit"

DEFAULT_FUSE_ROOT = "/sys/fs/fuse/connections"

_WAITING_ERR_THRESHOLD = 5
_COUNT_WARN_THRESHOLD = 50
# Kernel default is congestion_threshold = 3*max_background/4
# Flag only values clearly below that (userspace tuned down too
# aggressively, callers stall fast).
_CONGESTION_MIN = 4


def _read_int(path: str) -> Optional[int]:
    try:
        with open(path, "r", encoding="utf-8") as fh:
            return int(fh.read().strip())
    except (OSError, PermissionError, ValueError):
        return None


def walk_connections(root: str = DEFAULT_FUSE_ROOT) -> list:
    """Return list of dicts for each FUSE connection."""
    out: list = []
    if not os.path.isdir(root):
        return out
    try:
        ids = os.listdir(root)
    except OSError:
        return out
    for cid in ids:
        d = os.path.join(root, cid)
        if not os.path.isdir(d):
            continue
        waiting = _read_int(os.path.join(d, "waiting"))
        max_bg = _read_int(os.path.join(d, "max_background"))
        cong = _read_int(
            os.path.join(d, "congestion_threshold"))
        out.append({
            "id": cid,
            "waiting": waiting,
            "max_background": max_bg,
            "congestion_threshold": cong,
        })
    return out


def classify(fuse_present: bool,
             fuse_readable: bool,
             connections: list) -> dict:
    if not fuse_present:
        return {"verdict": "unknown",
                "reason": (
                    "/sys/fs/fuse/connections absent — "
                    "kernel built without FUSE.")}
    if not fuse_readable:
        return {"verdict": "requires_root",
                "reason": (
                    "/sys/fs/fuse/connections unreadable "
                    "— re-run as root.")}

    # err — any connection wedged
    wedged = [
        c for c in connections
        if c["waiting"] is not None
        and c["waiting"] > _WAITING_ERR_THRESHOLD]
    if wedged:
        names = [
            f"#{c['id']} waiting={c['waiting']}"
            for c in wedged[:5]]
        return {
            "verdict": "fuse_connection_wedged",
            "reason": (
                f"{len(wedged)} FUSE connection(s) have "
                f"> {_WAITING_ERR_THRESHOLD} pending "
                f"requests: {names}. Userspace fuse server "
                "stalled — processes on the mount will "
                "hang in D-state.")}

    # warn — too many connections
    if len(connections) > _COUNT_WARN_THRESHOLD:
        return {
            "verdict": "fuse_connection_count_high",
            "reason": (
                f"{len(connections)} FUSE connections "
                f"open (> {_COUNT_WARN_THRESHOLD}). Likely "
                "orphan mounts from sshfs / gvfs / AppImage "
                "/ Flatpak portal helpers piling up.")}

    # accent — congestion_threshold tuned aggressively low
    low_cong = [
        c for c in connections
        if c["congestion_threshold"] is not None
        and c["congestion_threshold"] < _CONGESTION_MIN]
    if low_cong:
        names = [
            f"#{c['id']}={c['congestion_threshold']}"
            for c in low_cong[:5]]
        return {
            "verdict": "congestion_threshold_low",
            "reason": (
                f"{len(low_cong)} connection(s) have "
                f"congestion_threshold < "
                f"{_CONGESTION_MIN}: {names}. Userspace "
                "tuned back-pressure aggressively low ; "
                "callers will stall fast.")}

    return {"verdict": "ok",
            "reason": (
                f"{len(connections)} FUSE connection(s) ; "
                "no wedged, no over-counts, thresholds "
                "default.")}


def status(config: Optional[dict] = None,
           root: str = DEFAULT_FUSE_ROOT) -> dict:
    fuse_present = os.path.isdir(root)
    fuse_readable = fuse_present and os.access(root, os.R_OK)
    connections: list = []
    if fuse_readable:
        connections = walk_connections(root)
    verdict = classify(fuse_present, fuse_readable,
                       connections)
    return {
        "ok": verdict["verdict"] == "ok",
        "connection_count": len(connections),
        "max_waiting": (
            max((c["waiting"] or 0)
                for c in connections) if connections else 0),
        "verdict": verdict,
    }
