"""Module nvidia_drm_params_audit — KMS-side nvidia driver
parameter posture (R&D #108.1).

Distinct from /sys/module/nvidia/parameters/* (RM-side, covered
by kmod_params) and /proc/driver/nvidia/* (covered by
nvidia_rm_audit). nvidia_drm is the KMS subsystem ; its modeset
parameter being off is the most common silent Wayland failure
on homelab desktops.

Reads :

  /sys/module/nvidia_drm/parameters/modeset       (0/1)
  /sys/module/nvidia_drm/parameters/fbdev         (0/1)

Verdicts (worst-first) :

  nvidia_drm_modeset_disabled   err     modeset=0 — Wayland,
                                        Sway, GBM allocators
                                        all break. Users hit
                                        this and don't know.
  nvidia_drm_fbdev_disabled     accent  fbdev=0 — no console
                                        takeover ; lock-screen
                                        blank on session crash.
  ok                                    modeset on, fbdev on.
  requires_root                         parameter files
                                        unreadable (mode 0400).
  unknown                               nvidia_drm module absent
                                        (non-NVIDIA host).

stdlib only.
"""
from __future__ import annotations

import os
from typing import Optional

NAME = "nvidia_drm_params_audit"

DEFAULT_SYSFS = "/sys/module/nvidia_drm/parameters"


def _read_text(path: str) -> Optional[str]:
    try:
        with open(path, "r", encoding="utf-8") as fh:
            return fh.read()
    except (OSError, PermissionError):
        return None


def _read_bool_param(path: str) -> Optional[bool]:
    t = _read_text(path)
    if t is None:
        return None
    s = t.strip().upper()
    if s in ("Y", "1", "TRUE"):
        return True
    if s in ("N", "0", "FALSE"):
        return False
    return None


def classify(module_present: bool,
             modeset: Optional[bool],
             fbdev: Optional[bool]) -> dict:
    if not module_present:
        return {"verdict": "unknown",
                "reason": (
                    "/sys/module/nvidia_drm absent — "
                    "nvidia_drm not loaded (non-NVIDIA "
                    "host or driver missing).")}
    if modeset is None and fbdev is None:
        return {"verdict": "requires_root",
                "reason": (
                    "nvidia_drm parameter files unreadable "
                    "(mode 0400) — re-run as root.")}

    if modeset is False:
        return {
            "verdict": "nvidia_drm_modeset_disabled",
            "reason": (
                "nvidia_drm.modeset=N — Wayland, Sway, "
                "GBM allocators won't work. Add "
                "`nvidia-drm.modeset=1` to GRUB cmdline.")}

    if fbdev is False:
        return {
            "verdict": "nvidia_drm_fbdev_disabled",
            "reason": (
                "nvidia_drm.fbdev=N — no console takeover. "
                "Lock-screen / TTY can go blank on session "
                "crash, leaving you with no way to recover "
                "short of SSH.")}

    return {"verdict": "ok",
            "reason": (
                f"modeset={modeset} ; fbdev={fbdev}. "
                "KMS posture coherent.")}


def status(config: Optional[dict] = None,
           sysfs: str = DEFAULT_SYSFS) -> dict:
    module_present = os.path.isdir(sysfs)
    modeset = (
        _read_bool_param(os.path.join(sysfs, "modeset"))
        if module_present else None)
    fbdev = (
        _read_bool_param(os.path.join(sysfs, "fbdev"))
        if module_present else None)
    verdict = classify(module_present, modeset, fbdev)
    return {
        "ok": verdict["verdict"] == "ok",
        "modeset": modeset,
        "fbdev": fbdev,
        "verdict": verdict,
    }
