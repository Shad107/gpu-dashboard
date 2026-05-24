"""Module v4l2_media_audit — V4L2 + media-controller + CEC
audit (R&D #74.4).

GPU encode workflows on a homelab — Sunshine, OBS, capture cards
feeding NVENC, HDMI CEC-aware compositors — hinge on three
related class trees :

  /sys/class/video4linux/<video*>/  per-V4L2 device-node
  /sys/class/media/<media*>/        media-controller topology
                                       (pipeline metadata)
  /sys/class/cec/<cec*>/             HDMI CEC adapters

Common failure modes :

* /dev/video* exists but is 0600 root → unprivileged OBS /
  Sunshine refuses to open it. The capture pipeline then falls
  back to slow software paths with no clear error.
* A V4L2 node is present but `device/driver` symlink missing →
  a module loaded then unloaded leaving a stale class entry.
* A capture device exposes /sys/class/video4linux/<n> but no
  matching /sys/class/media/<m> media-controller — modern
  apps that need topology metadata (e.g. CSI camera pipelines)
  silently fail.

Verdicts (priority order) :
  device_root_only_blocks_users        ≥1 /dev/video*, /dev/
                                         media*, /dev/cec*
                                         entry with mode 0600
                                         AND a corresponding
                                         sysfs class node
                                         present.
  driver_missing_kernel_module          ≥1 video4linux entry
                                         with no `device/driver`
                                         symlink (driver
                                         dropped, class entry
                                         stale).
  capture_node_present_no_media_controller
                                        video4linux entries
                                        present BUT
                                        /sys/class/media has
                                        none.
  stale_v4l_no_driver                   /sys/class/video4linux
                                         dir present but empty.
  ok                                    all sane.
  unknown                               none of the class trees
                                         present (kernel without
                                         CONFIG_VIDEO_DEV).

stdlib only.
"""
from __future__ import annotations

import os
import stat
from typing import List, Optional


NAME = "v4l2_media_audit"


_SYS_V4L = "/sys/class/video4linux"
_SYS_MEDIA = "/sys/class/media"
_SYS_CEC = "/sys/class/cec"
_DEV_ROOT = "/dev"


def _read(path: str) -> Optional[str]:
    try:
        with open(path) as f:
            return f.read().strip()
    except OSError:
        return None


def list_class(class_path: str) -> List[dict]:
    if not os.path.isdir(class_path):
        return []
    try:
        names = sorted(os.listdir(class_path))
    except OSError:
        return []
    out: List[dict] = []
    for n in names:
        d = os.path.join(class_path, n)
        if not (os.path.isdir(d) or os.path.islink(d)):
            continue
        driver = None
        drv_link = os.path.join(d, "device", "driver")
        try:
            driver = os.readlink(drv_link)
            driver = os.path.basename(driver)
        except OSError:
            pass
        out.append({"name": n, "driver": driver})
    return out


def dev_node_mode(name: str, dev_root: str = _DEV_ROOT
                       ) -> Optional[int]:
    """Returns POSIX mode bits for /dev/<name>, or None if
    missing."""
    try:
        st = os.stat(os.path.join(dev_root, name))
        return stat.S_IMODE(st.st_mode)
    except OSError:
        return None


def classify(v4l: List[dict], media: List[dict],
              cec: List[dict],
              v4l_present: bool, media_present: bool,
              cec_present: bool,
              dev_root_only: List[str]) -> dict:
    if not (v4l_present or media_present or cec_present):
        return {"verdict": "unknown",
                "reason": ("None of /sys/class/{video4linux,"
                          "media,cec} present — kernel without "
                          "CONFIG_VIDEO_DEV."),
                "recommendation": ""}

    # 1) device_root_only_blocks_users
    if dev_root_only:
        sample = ", ".join(dev_root_only[:3])
        return {"verdict": "device_root_only_blocks_users",
                "reason": (f"{len(dev_root_only)} /dev node(s) "
                          f"have mode 0600 root : {sample}. "
                          f"Unprivileged capture apps blocked."),
                "recommendation": _recipe_perms()}

    # 2) driver_missing_kernel_module
    orphan = [v for v in v4l if v.get("driver") is None]
    if orphan:
        sample = ", ".join(v["name"] for v in orphan[:3])
        return {"verdict": "driver_missing_kernel_module",
                "reason": (f"{len(orphan)} V4L2 device(s) without "
                          f"a bound driver : {sample}."),
                "recommendation": _recipe_driver()}

    # 3) capture_node_present_no_media_controller
    if v4l and not media:
        return {"verdict":
                    "capture_node_present_no_media_controller",
                "reason": (f"{len(v4l)} V4L2 capture device(s) "
                          f"present but no /sys/class/media "
                          f"entries — pipeline metadata "
                          f"unavailable."),
                "recommendation": _recipe_media_missing()}

    # 4) stale_v4l_no_driver
    if v4l_present and not v4l:
        return {"verdict": "stale_v4l_no_driver",
                "reason": ("/sys/class/video4linux directory "
                          "exists but is empty — driver dropped "
                          "leaving the class behind."),
                "recommendation": _recipe_stale()}

    return {"verdict": "ok",
            "reason": (f"v4l={len(v4l)} ; media={len(media)} ; "
                      f"cec={len(cec)}."),
            "recommendation": ""}


def status(config=None,
            sys_v4l: str = _SYS_V4L,
            sys_media: str = _SYS_MEDIA,
            sys_cec: str = _SYS_CEC,
            dev_root: str = _DEV_ROOT) -> dict:
    v4l_present = os.path.isdir(sys_v4l)
    media_present = os.path.isdir(sys_media)
    cec_present = os.path.isdir(sys_cec)
    v4l = list_class(sys_v4l)
    media = list_class(sys_media)
    cec = list_class(sys_cec)

    dev_root_only: List[str] = []
    for entry in v4l + media + cec:
        m = dev_node_mode(entry["name"], dev_root)
        if m == 0o600:
            dev_root_only.append(entry["name"])

    verdict = classify(v4l, media, cec,
                          v4l_present, media_present, cec_present,
                          dev_root_only)

    return {"ok": v4l_present or media_present or cec_present,
              "v4l_present": v4l_present,
              "media_present": media_present,
              "cec_present": cec_present,
              "v4l_count": len(v4l),
              "media_count": len(media),
              "cec_count": len(cec),
              "v4l_devices": v4l,
              "media_devices": media,
              "cec_devices": cec,
              "dev_root_only": dev_root_only,
              "verdict": verdict}


# ── recovery recipes ────────────────────────────────────────────

def _recipe_perms() -> str:
    return ("# /dev/video* / /dev/media* are 0600 root.\n"
            "# Expose to the 'video' group via udev :\n"
            "echo 'KERNEL==\"video*\", MODE=\"0660\", "
            "GROUP=\"video\"' \\\n"
            "  | sudo tee /etc/udev/rules.d/99-v4l2.rules\n"
            "echo 'KERNEL==\"media*\", MODE=\"0660\", "
            "GROUP=\"video\"' \\\n"
            "  | sudo tee -a /etc/udev/rules.d/99-v4l2.rules\n"
            "sudo udevadm trigger\n")


def _recipe_driver() -> str:
    return ("# A V4L2 class entry has no bound driver — module\n"
            "# dropped. Inspect :\n"
            "for v in /sys/class/video4linux/*; do\n"
            "  echo \"$v : driver=$(readlink $v/device/driver 2>/dev/null)\"\n"
            "done\n"
            "# Identify and reload the right module :\n"
            "sudo dmesg | grep -i v4l | tail\n")


def _recipe_media_missing() -> str:
    return ("# V4L2 devices visible but media-controller absent.\n"
            "# Install media-ctl tooling :\n"
            "sudo apt install v4l-utils\n"
            "media-ctl --help\n"
            "# Confirm kernel built with CONFIG_MEDIA_SUPPORT=y.\n")


def _recipe_stale() -> str:
    return ("# Empty /sys/class/video4linux — class registered\n"
            "# but no devices remained after driver unload.\n"
            "# Inspect modprobe history :\n"
            "sudo dmesg | grep -iE 'video4linux|v4l' | tail\n")
