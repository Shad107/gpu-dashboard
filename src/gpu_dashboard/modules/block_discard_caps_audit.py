"""Module block_discard_caps_audit — per-block-device TRIM /
discard cap posture (R&D #96.1).

Three modules touch block-storage surface but none read the
discard caps subtree :

  * trim_audit          — mount-option + fstrim.timer state
  * block_queue_audit   — scheduler / rotational / wbt
  * nvme_controller_state_audit — NVMe controller registers

This audit owns /sys/block/<dev>/queue/{discard_max_bytes,
discard_granularity} and the SCSI `provisioning_mode` quirk
that silently turns fstrim into a no-op.

Reads :

  /sys/block/<dev>/queue/discard_max_bytes
  /sys/block/<dev>/queue/discard_granularity
  /sys/block/<dev>/queue/rotational
  /sys/block/<dev>/device/scsi_disk/*/provisioning_mode
                                            (optional)

Verdicts (worst-first) :

  discard_disabled_on_ssd       err   any rotational=0
                                      device with
                                      discard_max_bytes = 0
                                      — TRIM can't run.
  provisioning_mode_full        warn  any SCSI device with
                                      provisioning_mode =
                                      'full' — fstrim
                                      succeeds but reclaims
                                      zero bytes.
  discard_granularity_huge      accent any device with
                                      discard_granularity ≥
                                      1 MiB — slow TRIM,
                                      may stall on bulk
                                      delete.
  discard_sane                  ok
  requires_root                 provisioning_mode mode-700
                                on hardened distros.
  unknown                       no real block devices (only
                                loop/ram/dm/md).

stdlib only.
"""
from __future__ import annotations

import os
from typing import Optional

NAME = "block_discard_caps_audit"

DEFAULT_SYS_BLOCK = "/sys/block"

# Prefixes to skip — these are virtual / synthetic devices,
# not real persistent storage worth auditing for TRIM caps.
_SKIP_PREFIXES = ("loop", "ram", "zram", "md", "dm-")

# Threshold for "huge granularity".
_HUGE_GRAN_BYTES = 1024 * 1024


def _read_text(path: str) -> Optional[str]:
    try:
        with open(path, "r", encoding="utf-8") as fh:
            return fh.read().strip()
    except (OSError, PermissionError):
        return None


def _read_int(path: str) -> Optional[int]:
    t = _read_text(path)
    if t is None:
        return None
    try:
        return int(t)
    except ValueError:
        return None


def _find_provisioning_mode(dev_root: str) -> Optional[str]:
    """SCSI provisioning_mode lives at
    /sys/block/<dev>/device/scsi_disk/*/provisioning_mode."""
    scsi = os.path.join(dev_root, "device", "scsi_disk")
    if not os.path.isdir(scsi):
        return None
    try:
        entries = os.listdir(scsi)
    except OSError:
        return None
    for name in entries:
        path = os.path.join(scsi, name, "provisioning_mode")
        t = _read_text(path)
        if t is not None:
            return t
    return None


def walk_block_devs(root: str = DEFAULT_SYS_BLOCK) -> list:
    if not os.path.isdir(root):
        return []
    try:
        names = sorted(os.listdir(root))
    except OSError:
        return []
    out: list = []
    for name in names:
        if any(name.startswith(p) for p in _SKIP_PREFIXES):
            continue
        dev_root = os.path.join(root, name)
        queue = os.path.join(dev_root, "queue")
        discard_max = _read_int(
            os.path.join(queue, "discard_max_bytes"))
        if discard_max is None:
            # No queue/discard files = not a real block dev.
            continue
        out.append({
            "name": name,
            "discard_max": discard_max,
            "discard_gran": _read_int(
                os.path.join(queue, "discard_granularity"))
            or 0,
            "rotational": _read_int(
                os.path.join(queue, "rotational")) or 0,
            "provisioning_mode":
                _find_provisioning_mode(dev_root) or "",
        })
    return out


def classify(devs: list) -> dict:
    if not devs:
        return {"verdict": "unknown",
                "reason": (
                    "No real block devices under /sys/block "
                    "— only virtual ones (loop/ram/dm/md). "
                    "Audit activates with persistent storage.")}

    # err — SSD with discard disabled
    bad_ssd = [
        d for d in devs
        if d["rotational"] == 0
        and d["discard_max"] == 0]
    if bad_ssd:
        names = [d["name"] for d in bad_ssd]
        return {
            "verdict": "discard_disabled_on_ssd",
            "reason": (
                f"{len(bad_ssd)} SSD-class device(s) with "
                f"discard_max_bytes=0: {names}. TRIM is "
                "kernel-disabled — performance will degrade "
                "as cells wear without re-mapping.")}

    # warn — provisioning_mode=full on SCSI
    full_prov = [
        d for d in devs
        if d["provisioning_mode"] == "full"]
    if full_prov:
        names = [d["name"] for d in full_prov]
        return {
            "verdict": "provisioning_mode_full",
            "reason": (
                f"{len(full_prov)} SCSI device(s) with "
                f"provisioning_mode = 'full': {names}. "
                "fstrim will return success but reclaim "
                "zero bytes — sysadmin must switch to "
                "'unmap' or 'writesame_zero'.")}

    # accent — huge granularity
    huge_gran = [
        d for d in devs
        if d["discard_gran"] >= _HUGE_GRAN_BYTES]
    if huge_gran:
        names = [d["name"] for d in huge_gran]
        return {
            "verdict": "discard_granularity_huge",
            "reason": (
                f"{len(huge_gran)} device(s) with "
                f"discard_granularity ≥ 1 MiB: {names}. "
                "Bulk fstrim may stall ; consider "
                "fstrim --offset/--length in chunks.")}

    return {"verdict": "discard_sane",
            "reason": (
                f"{len(devs)} real block device(s) ; all "
                "TRIM caps coherent.")}


def status(config: Optional[dict] = None,
           sys_block: str = DEFAULT_SYS_BLOCK) -> dict:
    devs = walk_block_devs(sys_block)
    verdict = classify(devs)
    return {
        "ok": verdict["verdict"] == "discard_sane",
        "device_count": len(devs),
        "verdict": verdict,
    }
