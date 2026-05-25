"""Module overlay_module_params_audit — overlayfs driver
parameter posture (R&D #108.2).

The container/snap overlayfs driver has several global tunables
that no existing module checks. kernel_module_params_drift_audit
tracks usbcore/zswap/ksm/nvme/xhci_hcd/i915/amdgpu — overlay
isn't in its list. fs_specific_tunables_audit is per-mount
(ext4/xfs/f2fs), not driver-global.

Reads :

  /sys/module/overlay/parameters/metacopy
    N (default) means a chmod on a large file triggers full
    copy_up to the upper layer — measurable container slowdown.
  /sys/module/overlay/parameters/redirect_dir
    N (default) breaks rename-on-directory for Docker / Podman /
    snap workflows.
  /sys/module/overlay/parameters/xino_auto
    Avoids st_ino collisions across layers. Y = on.
  /sys/module/overlay/parameters/redirect_always_follow

Verdicts (worst-first) :

  overlay_redirect_dir_off    warn    redirect_dir=N — Docker /
                                      Podman / snap rename-on-
                                      directory breaks.
  overlay_metacopy_off        accent  metadata-only copy
                                      disabled — chmod on big
                                      files copies whole file.
  overlay_xino_auto_off       accent  xino_auto=N — st_ino
                                      collisions across layers
                                      break rsync / find.
  ok                                  defaults sane.
  requires_root                       params unreadable.
  unknown                             overlay module absent.

stdlib only.
"""
from __future__ import annotations

import os
from typing import Optional

NAME = "overlay_module_params_audit"

DEFAULT_SYSFS = "/sys/module/overlay/parameters"


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
             metacopy: Optional[bool],
             redirect_dir: Optional[bool],
             xino_auto: Optional[bool]) -> dict:
    if not module_present:
        return {"verdict": "unknown",
                "reason": (
                    "overlay module not loaded — no "
                    "container / snap workflow uses it.")}
    if (metacopy is None and redirect_dir is None
            and xino_auto is None):
        return {"verdict": "requires_root",
                "reason": (
                    "overlay parameters unreadable — "
                    "re-run as root.")}

    if redirect_dir is False:
        return {
            "verdict": "overlay_redirect_dir_off",
            "reason": (
                "overlay.redirect_dir=N — Docker / Podman / "
                "snap rename-on-directory will fail. Enable "
                "via modprobe option redirect_dir=Y.")}

    if metacopy is False:
        return {
            "verdict": "overlay_metacopy_off",
            "reason": (
                "overlay.metacopy=N — metadata-only "
                "copy-up disabled. chmod on a large file "
                "rewrites the whole file. Bump with "
                "modprobe overlay metacopy=Y.")}

    if xino_auto is False:
        return {
            "verdict": "overlay_xino_auto_off",
            "reason": (
                "overlay.xino_auto=N — st_ino collisions "
                "across layers ; rsync / find on overlay "
                "mounts can misbehave.")}

    return {"verdict": "ok",
            "reason": (
                f"metacopy={metacopy} ; "
                f"redirect_dir={redirect_dir} ; "
                f"xino_auto={xino_auto}. Sane.")}


def status(config: Optional[dict] = None,
           sysfs: str = DEFAULT_SYSFS) -> dict:
    module_present = os.path.isdir(sysfs)
    metacopy = (
        _read_bool_param(os.path.join(sysfs, "metacopy"))
        if module_present else None)
    redirect_dir = (
        _read_bool_param(os.path.join(sysfs, "redirect_dir"))
        if module_present else None)
    xino_auto = (
        _read_bool_param(os.path.join(sysfs, "xino_auto"))
        if module_present else None)
    verdict = classify(module_present, metacopy,
                       redirect_dir, xino_auto)
    return {
        "ok": verdict["verdict"] == "ok",
        "metacopy": metacopy,
        "redirect_dir": redirect_dir,
        "xino_auto": xino_auto,
        "verdict": verdict,
    }
