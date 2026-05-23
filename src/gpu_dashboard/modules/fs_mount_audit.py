"""Module fs_mount_audit — Filesystem footgun detector for model dirs (R&D #23.2).

LLM / SD weights are read-heavy, mostly-sequential, gigabyte-class
files. Several common Linux FS configurations make this awful :

  - btrfs with transparent compression  (re-decompresses on every
    mmap page fault — measurable token/s loss)
  - NFS / CIFS / sshfs hosting weights  (every page touch = network
    roundtrip, even with `actimeo=N`)
  - ext4 with data=journal              (every write goes through
    journal — slow for ComfyUI output writes)
  - ecryptfs                            (per-file encryption layer
    that doubles read I/O)
  - tmpfs                               (volatile — loses model on
    shutdown ; great for RAM-cache but unsafe baseline)

This module maps `/proc/mounts` to the well-known LLM/AI model dirs
and flags any of the above. Read-only.

stdlib only.
"""
from __future__ import annotations

import os
from typing import Optional


NAME = "fs_mount_audit"


# Directories worth auditing (relative to $HOME or absolute).
KNOWN_MODEL_DIRS = [
    "~/.cache/huggingface",
    "~/.cache/torch",
    "~/.cache/llama.cpp",
    "~/.cache/lm-studio",
    "~/.lmstudio/models",
    "~/.ollama/models",
    "~/ComfyUI/models",
    "~/comfyui/models",
    "~/stable-diffusion-webui/models",
    "~/invokeai/models",
    "~/Fooocus/models",
    "~/SwarmUI/Models",
    "~/text-generation-webui/models",
    "~/models",
]


# (option_substr, severity, label, recommendation)
OPTION_FOOTGUNS = [
    ("compress=", "warn", "btrfs compression",
     "btrfs compress= adds CPU work on every read. Disable on the model "
     "subvol or move weights to ext4 / xfs."),
    ("compress-force=", "warn", "btrfs compress-force",
     "compress-force= forces compression. Switch off for model weights."),
    ("data=journal", "warn", "ext4 data=journal",
     "data=journal forces all writes through the journal — slow for "
     "ComfyUI output writes. Switch to data=ordered."),
]


_NET_FSTYPES = {"nfs", "nfs4", "cifs", "smb3", "sshfs", "fuse.sshfs"}
_RISKY_FSTYPES = {"ecryptfs", "tmpfs", "ramfs"}


def expand(p: str) -> str:
    return os.path.expanduser(p)


def parse_proc_mounts(path: str = "/proc/mounts") -> list[dict]:
    """Parse /proc/mounts → list of {device, mountpoint, fstype, options}."""
    out: list[dict] = []
    try:
        with open(path) as f:
            for line in f:
                parts = line.split()
                if len(parts) < 4:
                    continue
                out.append({
                    "device": _unescape(parts[0]),
                    "mountpoint": _unescape(parts[1]),
                    "fstype": parts[2],
                    "options": parts[3].split(","),
                })
    except OSError:
        return []
    return out


def _unescape(s: str) -> str:
    """/proc/mounts encodes spaces as \\040, etc."""
    return s.encode().decode("unicode_escape", errors="replace")


def find_mount_for(path: str, mounts: list[dict]) -> Optional[dict]:
    """Find the longest-prefix mount serving `path`."""
    abs_path = os.path.abspath(path)
    best: Optional[dict] = None
    best_len = -1
    for m in mounts:
        mp = m["mountpoint"]
        if abs_path == mp or abs_path.startswith(mp.rstrip("/") + "/"):
            if len(mp) > best_len:
                best = m
                best_len = len(mp)
    return best


def classify_mount(mount: dict) -> dict:
    """Return {severity, issues: [{label, recommendation}]} for a mount."""
    issues: list[dict] = []
    options = set(mount.get("options", []))
    fstype = mount.get("fstype", "")

    if fstype in _NET_FSTYPES:
        issues.append({
            "severity": "warn",
            "label": f"network filesystem ({fstype})",
            "recommendation": (f"{fstype} hosts model weights over the network. "
                                "Every page touch = roundtrip. Move weights to "
                                "local NVMe or add aggressive actimeo + cachefilesd."),
        })
    if fstype in _RISKY_FSTYPES:
        issues.append({
            "severity": "warn",
            "label": f"volatile filesystem ({fstype})",
            "recommendation": (f"{fstype} is volatile. OK as warm cache, "
                                "but you'll need to re-download on reboot."),
        })
    if fstype == "ecryptfs":
        issues[-1]["severity"] = "fail"

    # Option-based footguns
    for opt in options:
        for substr, severity, label, rec in OPTION_FOOTGUNS:
            if opt.startswith(substr):
                issues.append({"severity": severity,
                                "label": f"{label} ({opt})",
                                "recommendation": rec})
                break

    # NFS without noatime → every read updates inode atime + network call
    if fstype in _NET_FSTYPES and "noatime" not in options:
        issues.append({"severity": "warn",
                        "label": "atime updates on network FS",
                        "recommendation": ("Add `noatime` to your /etc/fstab "
                                            "entry — avoids one extra network "
                                            "write per read.")})

    severity = "ok"
    for i in issues:
        if i["severity"] == "fail":
            severity = "fail"
            break
        if i["severity"] == "warn":
            severity = "warn"
    return {"severity": severity, "issues": issues}


def audit_known_dirs(known_dirs: Optional[list[str]] = None,
                      mounts: Optional[list[dict]] = None) -> list[dict]:
    """For each existing known dir, find its mount + classify."""
    mounts = mounts if mounts is not None else parse_proc_mounts()
    targets = known_dirs or KNOWN_MODEL_DIRS
    out: list[dict] = []
    for d in targets:
        full = expand(d)
        if not os.path.isdir(full):
            continue
        mount = find_mount_for(full, mounts)
        if mount is None:
            continue
        verdict = classify_mount(mount)
        out.append({
            "directory": full,
            "mountpoint": mount["mountpoint"],
            "fstype": mount["fstype"],
            "options": mount["options"],
            "severity": verdict["severity"],
            "issues": verdict["issues"],
        })
    return out


def status(cfg=None) -> dict:
    """Aggregate snapshot."""
    audits = audit_known_dirs()
    warn_count = sum(1 for a in audits if a["severity"] == "warn")
    fail_count = sum(1 for a in audits if a["severity"] == "fail")
    summary_verdict: str
    summary_reason: str
    if not audits:
        summary_verdict = "no_dirs"
        summary_reason = "None of the known LLM/AI model directories exist."
    elif fail_count > 0:
        summary_verdict = "fail"
        summary_reason = (f"{fail_count} model dir(s) on filesystems with "
                          "serious footguns (ecryptfs, NFS+CIFS, etc.).")
    elif warn_count > 0:
        summary_verdict = "warn"
        summary_reason = (f"{warn_count} model dir(s) have FS options that "
                          "hurt LLM/SD read throughput.")
    else:
        summary_verdict = "ok"
        summary_reason = ("All known model dirs are on filesystems with "
                          "sane defaults.")
    return {
        "ok": True,
        "audits": audits,
        "audit_count": len(audits),
        "warn_count": warn_count,
        "fail_count": fail_count,
        "verdict": {
            "verdict": summary_verdict,
            "reason": summary_reason,
        },
    }
