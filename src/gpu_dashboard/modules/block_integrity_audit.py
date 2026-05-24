"""Module block_integrity_audit — T10-PI / DIF block-layer
integrity protection posture (R&D #83.1).

Enterprise NVMe and SAS drives ship with optional T10
Protection Information (PI) / Data Integrity Field (DIF)
support — every 4 KiB block carries an 8-byte CRC + tag
that the kernel and HBA verify on read.  Homelab users
running ex-datacenter drives often inherit drives configured
with PI but the kernel hands them out with read_verify = 0
(no shield) or format = "none" (PI never armed) — silent
data corruption can sit there for months.

Reads, per /sys/block/<dev>/integrity/ :

  device_is_integrity_capable   1 if the drive has PI
                                hardware (T10-PI, DIX).
  format                        "none" or "T10-DIF-TYPE1-CRC"
                                / "T10-DIF-TYPE3-CRC" etc.
  read_verify                   1 = kernel verifies CRC on
                                reads.
  write_generate                1 = kernel generates CRC on
                                writes.
  tag_size                      bytes of application tag.
  protection_interval_bytes     block size protected
                                (usually 4096 or 512).

Skips loop / ram / sr* devices.

Verdicts (worst first) :

  integrity_disabled_on_capable   device is integrity-
                                  capable AND format != none
                                  AND read_verify = 0 — PI
                                  hardware armed but kernel
                                  isn't checking, silent
                                  corruption shield off.
  integrity_unused                device is integrity-capable
                                  but format = "none" — PI
                                  hardware sitting unused.
  asymmetric_protection           write_generate = 1 but
                                  read_verify = 0 — writes
                                  carry CRC, reads don't
                                  check it.
  ok                              all integrity-capable
                                  devices have PI fully on,
                                  or no capable devices.
  n/a                             no block devices expose
                                  /integrity/ files (older
                                  kernel or no block storage).
"""
from __future__ import annotations

import os
import re
from typing import Optional

DEFAULT_BLOCK_ROOT = "/sys/block"

# Block devices we do NOT audit (virtual or removable).
_SKIP_PREFIXES = ("loop", "ram", "sr", "fd")


def _read_text(path: str) -> Optional[str]:
    try:
        with open(path, "r", encoding="utf-8") as fh:
            return fh.read().strip()
    except (OSError, PermissionError):
        return None


def _read_int(path: str) -> Optional[int]:
    s = _read_text(path)
    if s is None or s == "":
        return None
    try:
        return int(s)
    except ValueError:
        return None


def list_block_devices(root: str = DEFAULT_BLOCK_ROOT
                        ) -> list[str]:
    try:
        entries = sorted(os.listdir(root))
    except OSError:
        return []
    return [
        e for e in entries
        if not any(e.startswith(p) for p in _SKIP_PREFIXES)]


def read_integrity(root: str, dev: str) -> Optional[dict]:
    """Returns parsed integrity info, or None if the
    /integrity dir is absent."""
    idir = os.path.join(root, dev, "integrity")
    if not os.path.isdir(idir):
        return None
    return {
        "device": dev,
        "capable": _read_int(
            os.path.join(idir, "device_is_integrity_capable")),
        "format": _read_text(
            os.path.join(idir, "format")),
        "read_verify": _read_int(
            os.path.join(idir, "read_verify")),
        "write_generate": _read_int(
            os.path.join(idir, "write_generate")),
        "tag_size": _read_int(
            os.path.join(idir, "tag_size")),
        "protection_interval_bytes": _read_int(
            os.path.join(idir, "protection_interval_bytes")),
    }


def classify(devices: list[dict],
             any_integrity_dirs: bool) -> dict:
    if not any_integrity_dirs:
        return {"verdict": "n/a",
                "reason": (
                    "No /sys/block/*/integrity directories "
                    "found — kernel built without "
                    "CONFIG_BLK_DEV_INTEGRITY or no block "
                    "storage present.")}

    capable = [d for d in devices if d.get("capable") == 1]
    if not capable:
        return {"verdict": "ok",
                "reason": (
                    f"{len(devices)} block device(s) audited, "
                    "none integrity-capable — no T10-PI "
                    "hardware to worry about.")}

    # 1. err — capable + format set + read_verify off
    for d in capable:
        fmt = d.get("format") or "none"
        if (fmt != "none"
                and d.get("read_verify") == 0):
            return {
                "verdict": "integrity_disabled_on_capable",
                "reason": (
                    f"{d['device']} has integrity format "
                    f"{fmt} armed but read_verify = 0 — "
                    "kernel is not checking CRC on reads."),
                "device": d["device"], "format": fmt}

    # 2. warn — capable + format=none (PI hardware unused)
    for d in capable:
        if (d.get("format") or "none") == "none":
            return {"verdict": "integrity_unused",
                    "reason": (
                        f"{d['device']} is integrity-"
                        "capable but format = none — PI "
                        "hardware never armed."),
                    "device": d["device"]}

    # 3. accent — write_generate on, read_verify off
    for d in capable:
        if (d.get("write_generate") == 1
                and d.get("read_verify") == 0):
            return {"verdict": "asymmetric_protection",
                    "reason": (
                        f"{d['device']} generates CRC on "
                        "writes but does not verify on "
                        "reads — asymmetric protection."),
                    "device": d["device"]}

    return {"verdict": "ok",
            "reason": (
                f"{len(capable)} integrity-capable device(s) "
                "have PI fully on (format + read_verify + "
                "write_generate).")}


def status(config: Optional[dict] = None,
           root: str = DEFAULT_BLOCK_ROOT) -> dict:
    devs = list_block_devices(root)
    integrity: list[dict] = []
    any_dirs = False
    for d in devs:
        info = read_integrity(root, d)
        if info is None:
            continue
        any_dirs = True
        integrity.append(info)
    verdict = classify(integrity, any_dirs)
    return {
        "ok": verdict["verdict"] not in (
            "integrity_disabled_on_capable",),
        "device_count": len(integrity),
        "capable_count": sum(
            1 for d in integrity if d.get("capable") == 1),
        "devices": integrity,
        "verdict": verdict,
    }
