"""Module fs_quota_projid_audit — disk-quota / projid config
+ overlayfs upperdir-on-quota detection (R&D #98.2).

Real-world failure mode: a homelab ML-checkpoints volume is
silently throttled because prjquota was inherited from a
default mkfs profile and the projid file was lost or never
created. fstrim / writes return EDQUOT and the user blames
the disk. Or, overlayfs upperdir (containers, /var/lib/docker)
sits on a quota-enabled FS with no headroom, and the
container layer write starves.

The existing fs_mount_audit / vfs_limits_audit / bdi_writeback
modules cover mount tuning options, VFS sysctls, and bdi
writeback page quotas respectively. None of them parse
mount-side quota flags, /etc/projects orphan state, or the
overlay-upper-on-quota-FS topology.

Reads :

  /proc/mounts                       # quota mount options
  /proc/self/mountinfo               # overlay upperdir
  /etc/projects, /etc/projid         # iff present (read-only)

Verdicts (worst-first) :

  overlay_upper_on_quota_fs   warn   overlayfs upperdir
                                     resolves to a quota-
                                     enabled FS — container
                                     writes can hit EDQUOT.
  orphan_quota_config         accent prjquota mount option
                                     present but no
                                     /etc/projects — admin
                                     can't see / fix limits.
  quota_enabled_no_tools      accent quota mount option but
                                     no userspace quota tool
                                     installed (xfs_quota,
                                     quota, repquota).
  ok                                 no quotas, or quotas
                                     enabled + projects file
                                     present.
  requires_root                      mount table unreadable.
  unknown                            /proc/mounts absent.

stdlib only. No quotactl, no root needed.
"""
from __future__ import annotations

import os
from typing import Optional

NAME = "fs_quota_projid_audit"

DEFAULT_PROC_MOUNTS = "/proc/mounts"
DEFAULT_MOUNTINFO = "/proc/self/mountinfo"
DEFAULT_PROJECTS = "/etc/projects"
DEFAULT_PROJID = "/etc/projid"

# Mount options that enable any form of filesystem quota
_QUOTA_OPTS = (
    "usrquota", "grpquota", "prjquota",
    "uquota", "gquota", "pquota",
    "quota", "usrjquota", "grpjquota",
)

# Userspace quota tools (any one means the admin can read
# limits / usage)
_QUOTA_TOOLS = ("xfs_quota", "quota", "repquota", "edquota")


def _read_text(path: str) -> Optional[str]:
    try:
        with open(path, "r", encoding="utf-8") as fh:
            return fh.read()
    except (OSError, PermissionError):
        return None


def parse_proc_mounts(text: Optional[str]) -> list:
    """Return list of mount dicts with quota-related fields."""
    out: list = []
    if not text:
        return out
    for line in text.splitlines():
        parts = line.split()
        if len(parts) < 4:
            continue
        opts = parts[3].split(",")
        quota_opts = [o for o in opts if o in _QUOTA_OPTS]
        if not quota_opts:
            continue
        out.append({
            "device": parts[0],
            "mountpoint": parts[1],
            "fstype": parts[2],
            "quota_opts": quota_opts,
        })
    return out


def parse_mountinfo_overlays(text: Optional[str]) -> list:
    """Return overlay mounts with their upperdir path."""
    out: list = []
    if not text:
        return out
    for line in text.splitlines():
        parts = line.split()
        # mountinfo format: 36 35 98:0 /mnt1 /mnt2 ... - <fstype> ...
        # Find the '-' separator
        try:
            sep = parts.index("-")
        except ValueError:
            continue
        if sep + 1 >= len(parts):
            continue
        fstype = parts[sep + 1]
        if fstype != "overlay":
            continue
        mp = parts[4]
        # Super options come after the device field
        super_opts = (parts[sep + 3]
                       if sep + 3 < len(parts) else "")
        upperdir = ""
        for kv in super_opts.split(","):
            if kv.startswith("upperdir="):
                upperdir = kv.split("=", 1)[1]
                break
        out.append({"mountpoint": mp, "upperdir": upperdir})
    return out


def find_quota_for_path(quotas: list, path: str
                          ) -> Optional[dict]:
    """Find the most-specific quota mount that contains
    `path`. Returns the mount dict or None."""
    if not path:
        return None
    best: Optional[dict] = None
    best_len = -1
    for q in quotas:
        mp = q["mountpoint"]
        if path == mp or path.startswith(mp.rstrip("/") + "/"):
            if len(mp) > best_len:
                best = q
                best_len = len(mp)
    return best


def _which(tool: str) -> bool:
    paths = (os.environ.get("PATH")
             or "/usr/local/bin:/usr/bin:/bin:/sbin:/usr/sbin")
    for p in paths.split(os.pathsep):
        if os.path.isfile(os.path.join(p, tool)):
            return True
    return False


def classify(mounts_present: bool,
             mounts_readable: bool,
             quotas: list,
             overlays: list,
             projects_present: bool,
             quota_tools_present: bool) -> dict:
    if not mounts_present:
        return {"verdict": "unknown",
                "reason": (
                    "/proc/mounts absent — cannot inspect "
                    "mount table.")}
    if not mounts_readable:
        return {"verdict": "requires_root",
                "reason": (
                    "/proc/mounts unreadable — re-run as "
                    "root.")}

    if not quotas:
        return {"verdict": "ok",
                "reason": (
                    "No filesystem quotas enabled — "
                    "nothing to audit.")}

    # warn — overlay upperdir on a quota-enabled FS
    overlays_on_quota: list = []
    for ov in overlays:
        m = find_quota_for_path(quotas, ov["upperdir"])
        if m is not None:
            overlays_on_quota.append({
                "overlay_mp": ov["mountpoint"],
                "upperdir": ov["upperdir"],
                "quota_mp": m["mountpoint"]})
    if overlays_on_quota:
        sample = overlays_on_quota[0]
        return {
            "verdict": "overlay_upper_on_quota_fs",
            "reason": (
                f"{len(overlays_on_quota)} overlay mount(s) "
                f"have upperdir on a quota-enabled FS. "
                f"Example: {sample['overlay_mp']} upperdir="
                f"{sample['upperdir']} (under "
                f"{sample['quota_mp']}). Container writes "
                "can hit EDQUOT silently.")}

    # accent — prjquota enabled but no /etc/projects
    prj_quotas = [
        q for q in quotas
        if any(o in q["quota_opts"]
               for o in ("prjquota", "pquota"))]
    if prj_quotas and not projects_present:
        names = [q["mountpoint"] for q in prj_quotas]
        return {
            "verdict": "orphan_quota_config",
            "reason": (
                f"{len(prj_quotas)} mount(s) have prjquota "
                f"enabled but /etc/projects is absent: "
                f"{names}. Project IDs unlabeled — admin "
                "can't read / set limits.")}

    # accent — no userspace quota tool installed
    if not quota_tools_present:
        return {
            "verdict": "quota_enabled_no_tools",
            "reason": (
                f"{len(quotas)} quota-enabled mount(s) but "
                "no userspace quota tool (xfs_quota / "
                "quota / repquota / edquota) found in PATH. "
                "Limits are silent.")}

    return {"verdict": "ok",
            "reason": (
                f"{len(quotas)} quota-enabled mount(s) ; "
                "projects file present, tools installed.")}


def status(config: Optional[dict] = None,
           proc_mounts: str = DEFAULT_PROC_MOUNTS,
           mountinfo: str = DEFAULT_MOUNTINFO,
           projects: str = DEFAULT_PROJECTS) -> dict:
    mounts_present = os.path.isfile(proc_mounts)
    mounts_text = (_read_text(proc_mounts)
                   if mounts_present else None)
    mounts_readable = mounts_text is not None
    quotas = parse_proc_mounts(mounts_text)

    mountinfo_text = _read_text(mountinfo)
    overlays = parse_mountinfo_overlays(mountinfo_text)

    projects_present = os.path.isfile(projects)
    quota_tools_present = any(_which(t) for t in _QUOTA_TOOLS)

    verdict = classify(
        mounts_present, mounts_readable,
        quotas, overlays,
        projects_present, quota_tools_present)
    return {
        "ok": verdict["verdict"] == "ok",
        "quota_mount_count": len(quotas),
        "overlay_count": len(overlays),
        "projects_present": projects_present,
        "tools_present": quota_tools_present,
        "verdict": verdict,
    }
