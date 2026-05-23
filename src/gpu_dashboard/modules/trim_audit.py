"""Module trim_audit — TRIM / discard auditor (R&D #25.2).

When model weights (50-200 GiB) are repeatedly written + deleted on
an SSD, missing TRIM support causes silent write-amplification :
the controller can't free internal blocks, and your effective
endurance drops 2-3×.

Two knobs that need to align :
  1. The filesystem mount must use `discard` (online TRIM), OR
  2. The `fstrim.timer` systemd unit must be enabled+active for
     weekly batch TRIM.

This module complements R&D #23.2 FS audit by adding the
SSD-lifecycle dimension. Pure /proc/mounts + systemctl. No sudo.

stdlib only.
"""
from __future__ import annotations

import os
import shutil
import subprocess
from typing import Optional


NAME = "trim_audit"


# Known model-cache directories (same list as fs_mount_audit).
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


_NET_FSTYPES = {"nfs", "nfs4", "cifs", "smb3", "sshfs", "fuse.sshfs"}
_VOLATILE_FSTYPES = {"tmpfs", "ramfs"}


def expand(p: str) -> str:
    return os.path.expanduser(p)


def parse_proc_mounts(path: str = "/proc/mounts") -> list[dict]:
    """Parse /proc/mounts → list of mounts."""
    out: list[dict] = []
    try:
        with open(path) as f:
            for line in f:
                parts = line.split()
                if len(parts) < 4:
                    continue
                out.append({
                    "device": parts[0],
                    "mountpoint": parts[1].encode().decode(
                        "unicode_escape", errors="replace"),
                    "fstype": parts[2],
                    "options": parts[3].split(","),
                })
    except OSError:
        return []
    return out


def find_mount_for(path: str, mounts: list[dict]) -> Optional[dict]:
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


def device_basename(device_path: str) -> Optional[str]:
    """'/dev/nvme0n1p2' → 'nvme0n1'  ; '/dev/sda1' → 'sda'."""
    if not device_path.startswith("/dev/"):
        return None
    name = device_path[len("/dev/"):]
    # Strip partition suffix
    import re
    if name.startswith("nvme"):
        # nvme0n1p2 → nvme0n1
        m = re.match(r"(nvme\d+n\d+)", name)
        return m.group(1) if m else None
    # sdaN → sda
    m = re.match(r"([a-z]+)", name)
    return m.group(1) if m else None


def is_rotational(device_basename: str,
                   sys_root: str = "/sys/block") -> Optional[bool]:
    """True = HDD (TRIM N/A), False = SSD/NVMe, None = unknown."""
    p = os.path.join(sys_root, device_basename, "queue", "rotational")
    try:
        with open(p) as f:
            return f.read().strip() == "1"
    except OSError:
        return None


def fstrim_timer_state(timeout: float = 2.0) -> dict:
    """Return {enabled, active} for fstrim.timer."""
    out = {"enabled": None, "active": None}
    if not shutil.which("systemctl"):
        return out
    for key, arg in (("enabled", "is-enabled"), ("active", "is-active")):
        try:
            r = subprocess.run(
                ["systemctl", arg, "fstrim.timer"],
                capture_output=True, text=True, timeout=timeout,
            )
            out[key] = r.stdout.strip() or None
        except (FileNotFoundError, subprocess.TimeoutExpired, OSError):
            continue
    return out


def audit_one_dir(path: str, mounts: list[dict]) -> Optional[dict]:
    """Per-dir TRIM audit. Returns None if path missing."""
    if not os.path.isdir(path):
        return None
    mount = find_mount_for(path, mounts)
    if mount is None:
        return None
    options = set(mount.get("options", []))
    fstype = mount.get("fstype", "")
    has_discard = "discard" in options
    on_ssd: Optional[bool] = None
    if fstype in _NET_FSTYPES or fstype in _VOLATILE_FSTYPES:
        on_ssd = False  # TRIM doesn't apply
    else:
        base = device_basename(mount["device"])
        if base:
            rot = is_rotational(base)
            on_ssd = (False if rot is True else
                       True if rot is False else None)
    return {
        "directory": path,
        "mountpoint": mount["mountpoint"],
        "device": mount["device"],
        "fstype": fstype,
        "has_discard_mount": has_discard,
        "on_ssd": on_ssd,
    }


def classify(audits: list[dict], timer: dict) -> dict:
    """Return {verdict, reason, recommendation}."""
    if not audits:
        return {"verdict": "no_dirs",
                "reason": "No model directories on this system.",
                "recommendation": ""}
    ssd_audits = [a for a in audits if a.get("on_ssd")]
    if not ssd_audits:
        return {"verdict": "no_ssd",
                "reason": ("Model dirs are not on SSD / NVMe — TRIM "
                           "doesn't apply."),
                "recommendation": ""}
    timer_running = timer.get("enabled") == "enabled" and \
                    timer.get("active") == "active"
    any_inline = any(a["has_discard_mount"] for a in ssd_audits)
    if timer_running or any_inline:
        return {"verdict": "ok",
                "reason": ("Either fstrim.timer is active or `discard` is "
                           "set on the SSD mount — TRIM is happening."),
                "recommendation": ""}
    return {"verdict": "no_trim",
            "reason": ("SSD model dirs without `discard` mount option AND "
                       "fstrim.timer is not active. Write amplification "
                       "will silently shorten SSD life."),
            "recommendation": "sudo systemctl enable --now fstrim.timer"}


def status(cfg=None) -> dict:
    """Aggregate snapshot."""
    mounts = parse_proc_mounts()
    audits: list[dict] = []
    for d in KNOWN_MODEL_DIRS:
        full = expand(d)
        rec = audit_one_dir(full, mounts)
        if rec is not None:
            audits.append(rec)
    timer = fstrim_timer_state()
    verdict = classify(audits, timer)
    return {
        "ok": True,
        "audits": audits,
        "audit_count": len(audits),
        "fstrim_timer": timer,
        "verdict": verdict,
    }
