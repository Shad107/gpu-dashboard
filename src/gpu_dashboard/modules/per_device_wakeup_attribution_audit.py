"""Module per_device_wakeup_attribution_audit — per-device
wakeup attribution under /sys/devices (R&D #95.4).

Two existing modules touch wakeup surface :

  * wakeup_sources_audit  — /sys/class/wakeup/wakeup*/
                           (named source aggregates) +
                           /sys/power/wakeup_count
  * suspend_stats_audit   — /sys/power/suspend_stats post-
                           mortem

This audit walks the per-device /sys/devices/.../power/
attributes — finer-grained than the wakeup-source-named
aggregates because it names the EXACT device (e.g.
'0000:00:14.0' xHCI, 'i2c-ELAN9009') that woke the box.

Reads :

  /sys/devices/.../power/wakeup            'enabled'/'disabled'
  /sys/devices/.../power/wakeup_count
  /sys/devices/.../power/wakeup_active_count
  /sys/devices/.../power/wakeup_abort_count
  /sys/devices/.../power/wakeup_max_time_ms
  /sys/devices/.../power/wakeup_total_time_ms

Verdicts (worst-first) :

  wakeup_storm_blocking_suspend  err   any device with
                                       wakeup_count > 100
                                       AND wakeup_max_time_ms
                                       > 5000 — preventing
                                       s2idle.
  wakeup_aborts_climbing          warn  any device with
                                       wakeup_abort_count >
                                       10 — interrupted
                                       suspend attempts.
  wakeup_enabled_on_unused_device accent device with
                                       wakeup=enabled but
                                       wakeup_count=0 since
                                       boot (USB / I²C left
                                       enabled by default).
  wakeup_attribution_clean        ok   nothing notable.
  requires_root                   power/* mode-700.
  unknown                         /sys/devices absent.

stdlib only.
"""
from __future__ import annotations

import os
from typing import Optional

NAME = "per_device_wakeup_attribution_audit"

DEFAULT_SYS_DEVICES = "/sys/devices"

# Bound the walk on systems with thousands of devices.
_MAX_DEVICES = 2000
# Storm thresholds.
_STORM_COUNT_THRESHOLD = 100
_STORM_MAX_MS_THRESHOLD = 5000
_ABORT_THRESHOLD = 10


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


def _kind_for_path(devpath: str) -> str:
    """Best-effort device-kind tag from path components."""
    if "/usb" in devpath:
        return "usb"
    if "/i2c-" in devpath:
        return "i2c"
    if "/pci" in devpath:
        return "pci"
    return "other"


def walk_devices(root: str = DEFAULT_SYS_DEVICES,
                 max_devices: int = _MAX_DEVICES
                 ) -> list:
    """Return list of dicts {path, kind, wakeup, count,
    active_count, abort_count, max_ms, total_ms} for each
    device that has a power/wakeup file."""
    if not os.path.isdir(root):
        return []
    out: list = []
    seen = 0
    for dirpath, dirnames, filenames in os.walk(root):
        # Bound walk size.
        if seen >= max_devices:
            break
        if os.path.basename(dirpath) != "power":
            continue
        if "wakeup" not in filenames:
            continue
        seen += 1
        wakeup_state = _read_text(
            os.path.join(dirpath, "wakeup")) or ""
        # 'disabled' / 'enabled' / sometimes empty
        if wakeup_state not in ("enabled", "disabled"):
            continue
        devpath = os.path.dirname(dirpath)
        out.append({
            "path": os.path.relpath(devpath, root),
            "kind": _kind_for_path(devpath),
            "wakeup": wakeup_state,
            "count": _read_int(
                os.path.join(dirpath, "wakeup_count")) or 0,
            "active_count": _read_int(
                os.path.join(
                    dirpath, "wakeup_active_count")) or 0,
            "abort_count": _read_int(
                os.path.join(
                    dirpath, "wakeup_abort_count")) or 0,
            "max_ms": _read_int(
                os.path.join(
                    dirpath, "wakeup_max_time_ms")) or 0,
            "total_ms": _read_int(
                os.path.join(
                    dirpath, "wakeup_total_time_ms")) or 0,
        })
    return out


def classify(devices: list, root_present: bool) -> dict:
    if not root_present:
        return {"verdict": "unknown",
                "reason": (
                    "/sys/devices absent — no device tree to "
                    "walk.")}
    if not devices:
        return {"verdict": "unknown",
                "reason": (
                    "No /sys/devices/.../power/wakeup files "
                    "found — kernel built without device-PM "
                    "or unusual platform.")}

    # err — wakeup storm preventing suspend
    storms = [
        d for d in devices
        if d["count"] > _STORM_COUNT_THRESHOLD
        and d["max_ms"] > _STORM_MAX_MS_THRESHOLD]
    if storms:
        names = sorted((s["path"] for s in storms))[:3]
        return {
            "verdict": "wakeup_storm_blocking_suspend",
            "reason": (
                f"{len(storms)} device(s) with > "
                f"{_STORM_COUNT_THRESHOLD} wakeups AND > "
                f"{_STORM_MAX_MS_THRESHOLD} ms max-time: "
                f"{names}. Suspending will be aborted "
                "repeatedly.")}

    # warn — suspend aborts
    aborts = [d for d in devices
              if d["abort_count"] > _ABORT_THRESHOLD]
    if aborts:
        names = sorted(
            (a["path"] for a in aborts),
            key=lambda p: -[a["abort_count"]
                             for a in aborts
                             if a["path"] == p][0])[:3]
        return {
            "verdict": "wakeup_aborts_climbing",
            "reason": (
                f"{len(aborts)} device(s) with > "
                f"{_ABORT_THRESHOLD} aborted suspends: "
                f"{names}. Interrupting suspend in progress "
                "— check dmesg for the offender.")}

    # accent — wakeup enabled but never used (USB/I2C
    # devices commonly default to enabled)
    unused = [
        d for d in devices
        if d["wakeup"] == "enabled" and d["count"] == 0
        and d["kind"] in ("usb", "i2c")]
    if unused:
        names = sorted((u["path"] for u in unused))[:3]
        return {
            "verdict": "wakeup_enabled_on_unused_device",
            "reason": (
                f"{len(unused)} USB/I²C device(s) have "
                f"wakeup=enabled but wakeup_count=0 (never "
                f"woke the system, e.g. {names}). Power "
                "saved if disabled.")}

    return {"verdict": "wakeup_attribution_clean",
            "reason": (
                f"{len(devices)} device(s) with wakeup attrs "
                "inspected ; no storms, no aborts, no idle "
                "wakeup-enabled devices.")}


def status(config: Optional[dict] = None,
           sys_devices: str = DEFAULT_SYS_DEVICES) -> dict:
    root_present = os.path.isdir(sys_devices)
    devices = (walk_devices(sys_devices)
               if root_present else [])
    verdict = classify(devices, root_present)
    return {
        "ok": (verdict["verdict"]
               == "wakeup_attribution_clean"),
        "device_count": len(devices),
        "enabled_count": sum(
            1 for d in devices if d["wakeup"] == "enabled"),
        "verdict": verdict,
    }
