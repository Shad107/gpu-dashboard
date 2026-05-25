"""Module nfs_mountstats_audit — NFS RPC transport health
auditor (R&D #93.3).

Existing modules touch related surface but never the NFS
RPC plane :

  * fs_mount_audit         — mount flag posture only
  * fs_specific_tunables_audit — ext4 / xfs / f2fs / btrfs
  * nfsd_stats_audit       — the *server* side
  * net_iface_counters_audit — no NFS-RPC awareness

Reads :

  /proc/self/mountstats     — per-mount RPC counters for any
                              NFS / NFSv4 mount. The 'xprt:
                              tcp <port> <bind> <connect>
                              <ct> <idle> <sends> <recvs>
                              <bad_xids> <req_u> <bk_u>' line
                              holds the transport-level
                              counts we care about.

Verdicts (worst-first) :

  xprt_reconnect_storm     err   any NFS mount with
                                 connect_count > 5 — the
                                 transport keeps tearing down
                                 (LAN issue, server flapping,
                                 firewall reset).
  bad_xids_present         warn  any mount with bad_xids > 0
                                 — protocol mismatches or
                                 replay bugs on the server.
  many_nfs_mounts          accent > 10 NFS mounts mounted —
                                 incident attribution gets
                                 harder, consider whether
                                 all are needed.
  ok                       healthy NFS surface or empty.
  no_nfs_mounts            ok-informational, no NFS mounts.
  requires_root            /proc/self/mountstats unreadable
                           (some hardened distros mode-600 it).
  unknown                  file absent (no procfs).

The proposed slow_op_rtt and op_timeout_rate_high verdicts
were dropped — both require per-op multi-line aggregation +
delta tracking that explodes the audit's complexity for
marginal real-world value on a homelab NFS mount.

stdlib only.
"""
from __future__ import annotations

import os
import re
from typing import Optional

NAME = "nfs_mountstats_audit"

DEFAULT_MOUNTSTATS = "/proc/self/mountstats"

# Threshold for reconnect storm (cumulative since mount).
_RECONNECT_STORM_THRESHOLD = 5
# Threshold for many-mounts accent.
_MANY_MOUNTS_THRESHOLD = 10

_DEVICE_RE = re.compile(
    r"^device (\S+) mounted on (\S+) with fstype (nfs\w*)\b")
_XPRT_TCP_RE = re.compile(
    r"^\s*xprt:\s+tcp\s+(.+)$")


def _read_text(path: str) -> Optional[str]:
    try:
        with open(path, "r", encoding="utf-8") as fh:
            return fh.read()
    except (OSError, PermissionError):
        return None


def parse_mountstats(text: str) -> list:
    """Return list of dicts {device, mountpoint, fstype,
    xprt: {connect_count, sends, recvs, bad_xids}} for each
    NFS mount section in /proc/self/mountstats."""
    if not text:
        return []
    out: list = []
    current: Optional[dict] = None
    for line in text.splitlines():
        m = _DEVICE_RE.match(line)
        if m:
            if (current is not None
                    and current.get("fstype", "").startswith(
                        "nfs")):
                out.append(current)
            current = {
                "device": m.group(1),
                "mountpoint": m.group(2),
                "fstype": m.group(3),
                "connect_count": None,
                "sends": None,
                "recvs": None,
                "bad_xids": None,
            }
            continue
        if current is None:
            continue
        m = _XPRT_TCP_RE.match(line)
        if m:
            parts = m.group(1).split()
            # tcp xprt fields :
            #   <port> <bind_count> <connect_count>
            #   <connect_time> <idle_time> <sends> <recvs>
            #   <bad_xids> <req_u> <bk_u>
            if len(parts) >= 8:
                try:
                    current["connect_count"] = int(parts[2])
                    current["sends"] = int(parts[5])
                    current["recvs"] = int(parts[6])
                    current["bad_xids"] = int(parts[7])
                except ValueError:
                    pass
    if (current is not None
            and current.get("fstype", "").startswith("nfs")):
        out.append(current)
    return out


def classify(mounts: list, file_present: bool,
             file_readable: bool) -> dict:
    if not file_present:
        return {"verdict": "unknown",
                "reason": (
                    "/proc/self/mountstats absent — procfs "
                    "unavailable.")}
    if not file_readable:
        return {"verdict": "requires_root",
                "reason": (
                    "/proc/self/mountstats unreadable — "
                    "some hardened distros mode-600 it. "
                    "Re-run as root.")}

    if not mounts:
        return {"verdict": "no_nfs_mounts",
                "reason": (
                    "/proc/self/mountstats readable but no "
                    "NFS / NFSv4 mounts found.")}

    # err — reconnect storm on any mount
    storm = [m for m in mounts
             if (m.get("connect_count") or 0)
             > _RECONNECT_STORM_THRESHOLD]
    if storm:
        names = [
            f"{m['device']}→{m['mountpoint']}"
            for m in storm]
        return {
            "verdict": "xprt_reconnect_storm",
            "reason": (
                f"{len(storm)} NFS mount(s) have "
                f"connect_count > {_RECONNECT_STORM_THRESHOLD} "
                f"(e.g. {names[:2]}). RPC transport is "
                "tearing down — LAN flap / firewall reset / "
                "server reboots.")}

    # warn — any mount with bad_xids
    bad = [m for m in mounts
           if (m.get("bad_xids") or 0) > 0]
    if bad:
        names = [
            f"{m['device']}→{m['mountpoint']}"
            for m in bad]
        return {
            "verdict": "bad_xids_present",
            "reason": (
                f"{len(bad)} NFS mount(s) have non-zero "
                f"bad_xids (e.g. {names[:2]}). RPC protocol "
                "mismatches or replay anomalies.")}

    # accent — many mounts
    if len(mounts) > _MANY_MOUNTS_THRESHOLD:
        return {
            "verdict": "many_nfs_mounts",
            "reason": (
                f"{len(mounts)} NFS mounts active — "
                "incident attribution harder ; consider "
                "consolidating.")}

    return {"verdict": "ok",
            "reason": (
                f"{len(mounts)} NFS mount(s) ; zero "
                "reconnect storms, zero bad_xids.")}


def status(config: Optional[dict] = None,
           mountstats_path: str = DEFAULT_MOUNTSTATS) -> dict:
    file_present = os.path.isfile(mountstats_path)
    text = (_read_text(mountstats_path)
            if file_present else None)
    file_readable = text is not None
    mounts = parse_mountstats(text or "")
    verdict = classify(mounts, file_present, file_readable)
    return {
        "ok": verdict["verdict"] in ("ok", "no_nfs_mounts"),
        "nfs_mount_count": len(mounts),
        "verdict": verdict,
    }
