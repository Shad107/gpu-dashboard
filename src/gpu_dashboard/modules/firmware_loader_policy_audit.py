"""Module firmware_loader_policy_audit — firmware loader
policy + timeout (R&D #104.3).

When a driver requests firmware via request_firmware(), the
kernel first asks udev, and (depending on policy) optionally
falls back to a sysfs blob-upload path. On cold boot with a
slow USB camera or out-of-tree WiFi card, a too-short timeout
or a blocked fallback path silently fails the firmware load.

  /sys/class/firmware/timeout                 # seconds
  /sys/kernel/firmware_config/force_sysfs_fallback   # 0/1
  /sys/kernel/firmware_config/ignore_sysfs_fallback  # 0/1

The existing spi_firmware_loader_audit handles SPI-specific
blobs. No module reads /sys/class/firmware/timeout or
firmware_config/* sysfs.

Reads :

  /sys/class/firmware/timeout
  /sys/kernel/firmware_config/force_sysfs_fallback
  /sys/kernel/firmware_config/ignore_sysfs_fallback

Verdicts (worst-first) :

  fw_fallback_disabled    warn    ignore_sysfs_fallback=1 —
                                  sysfs upload path blocked.
                                  Out-of-tree drivers that
                                  ship their own blob fail.
  fw_timeout_too_short    warn    /sys/class/firmware/timeout
                                  < 10 s — slow USB / Wi-Fi
                                  firmware loads time out.
  fw_fallback_forced      accent  force_sysfs_fallback=1 —
                                  udev path bypassed, sysfs
                                  always used. Unusual.
  ok                              defaults intact.
  requires_root                   sysfs unreadable.
  unknown                         /sys/class/firmware absent.

stdlib only.
"""
from __future__ import annotations

import os
from typing import Optional

NAME = "firmware_loader_policy_audit"

DEFAULT_FW_CLASS = "/sys/class/firmware"
DEFAULT_FW_CONFIG = "/sys/kernel/firmware_config"

_TIMEOUT_MIN_S = 10


def _read_int(path: str) -> Optional[int]:
    try:
        with open(path, "r", encoding="utf-8") as fh:
            return int(fh.read().strip())
    except (OSError, PermissionError, ValueError):
        return None


def classify(class_present: bool,
             timeout: Optional[int],
             force_fallback: Optional[int],
             ignore_fallback: Optional[int]) -> dict:
    if not class_present:
        return {"verdict": "unknown",
                "reason": (
                    "/sys/class/firmware absent — kernel "
                    "without firmware loader.")}
    if timeout is None:
        return {"verdict": "requires_root",
                "reason": (
                    "/sys/class/firmware/timeout "
                    "unreadable — re-run as root.")}

    # warn — sysfs fallback ignored
    if ignore_fallback == 1:
        return {
            "verdict": "fw_fallback_disabled",
            "reason": (
                "firmware_config.ignore_sysfs_fallback=1 — "
                "sysfs upload path is blocked. Out-of-tree "
                "drivers that ship their own blob will "
                "fail to load.")}

    # warn — timeout too short
    if timeout < _TIMEOUT_MIN_S:
        return {
            "verdict": "fw_timeout_too_short",
            "reason": (
                f"/sys/class/firmware/timeout={timeout} s "
                f"(< {_TIMEOUT_MIN_S}). Slow USB cameras / "
                "Wi-Fi cards / FPGA blobs may time out at "
                "cold boot.")}

    # accent — sysfs fallback forced
    if force_fallback == 1:
        return {
            "verdict": "fw_fallback_forced",
            "reason": (
                "firmware_config.force_sysfs_fallback=1 — "
                "udev firmware path bypassed, sysfs always "
                "used. Unusual on a modern desktop.")}

    return {"verdict": "ok",
            "reason": (
                f"timeout={timeout} s ; "
                f"force_fallback={force_fallback} ; "
                f"ignore_fallback={ignore_fallback}. Sane.")}


def status(config: Optional[dict] = None,
           fw_class: str = DEFAULT_FW_CLASS,
           fw_config: str = DEFAULT_FW_CONFIG) -> dict:
    class_present = os.path.isdir(fw_class)
    timeout = (
        _read_int(os.path.join(fw_class, "timeout"))
        if class_present else None)
    force_fallback = _read_int(
        os.path.join(fw_config, "force_sysfs_fallback"))
    ignore_fallback = _read_int(
        os.path.join(fw_config, "ignore_sysfs_fallback"))
    verdict = classify(class_present, timeout,
                       force_fallback, ignore_fallback)
    return {
        "ok": verdict["verdict"] == "ok",
        "timeout_s": timeout,
        "force_sysfs_fallback": force_fallback,
        "ignore_sysfs_fallback": ignore_fallback,
        "verdict": verdict,
    }
