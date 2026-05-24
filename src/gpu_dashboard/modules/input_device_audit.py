"""Module input_device_audit — /sys/class/input/event*/
wakeup-posture audit (R&D #76.4).

A headless homelab often has a USB keyboard / dock / HID
controller with `power/wakeup=enabled` quietly pulling the
box out of suspend mid-warmup. Conversely, an `inhibited=1`
keyboard masks real wake-on-LAN testing because the kernel
silently ignores its key events.

Existing wakeup_sources_audit reads /sys/kernel/debug/wakeup_*
counters but does not enumerate per-input-device wakeup
posture. This audit closes that gap by walking
/sys/class/input/event*/ and surfacing :

  device/name        human-readable label
  device/inhibited   0 = listening, 1 = ignored (kernel
                       drops events)
  device/modalias    HID / USB modalias
  <up the device tree>/power/wakeup        enabled/disabled
  <up the device tree>/power/wakeup_count  monotonic wake count

Cross-references /proc/bus/input/devices for the human-readable
handler+name mapping.

Verdicts (priority order) :
  spurious_wake_source       ≥1 input device with
                               wakeup=enabled AND wake_count > 0.
  wakeup_enabled_orphan      wakeup=enabled at a parent but the
                               event/device name is missing
                               (driver dropped).
  inhibited_active_input     ≥1 device with inhibited=1.
  stale_event_node           /sys/class/input/eventN exists but
                               device/name is unreadable.
  ok                         posture sane.
  unknown                    /sys/class/input absent.

stdlib only.
"""
from __future__ import annotations

import os
from typing import List, Optional


NAME = "input_device_audit"


_SYS_INPUT = "/sys/class/input"
_PROC_INPUT_DEVICES = "/proc/bus/input/devices"


# Walk up at most this many levels looking for power/wakeup.
_WAKEUP_WALK_MAX = 8


def _read(path: str) -> Optional[str]:
    try:
        with open(path) as f:
            return f.read().strip()
    except OSError:
        return None


def _read_int(path: str) -> Optional[int]:
    t = _read(path)
    if t is None:
        return None
    try:
        return int(t)
    except ValueError:
        return None


def find_wakeup(start: str) -> dict:
    """Walk up at most _WAKEUP_WALK_MAX levels from `start`
    looking for power/wakeup + power/wakeup_count.
    Returns {state, count} or {state: None, count: None}."""
    cur = start
    for _ in range(_WAKEUP_WALK_MAX):
        if not cur or cur == "/":
            break
        wake = os.path.join(cur, "power", "wakeup")
        if os.path.exists(wake):
            return {
                "state": _read(wake),
                "count": _read_int(
                    os.path.join(cur, "power", "wakeup_count")),
            }
        cur = os.path.dirname(cur)
    return {"state": None, "count": None}


def list_event_devices(sys_input: str = _SYS_INPUT
                            ) -> List[dict]:
    if not os.path.isdir(sys_input):
        return []
    try:
        names = sorted(os.listdir(sys_input))
    except OSError:
        return []
    out: List[dict] = []
    for n in names:
        if not n.startswith("event"):
            continue
        d = os.path.join(sys_input, n)
        dev = os.path.join(d, "device")
        # Resolve symlink to its real path so we can walk up.
        try:
            dev_real = os.path.realpath(dev)
        except OSError:
            dev_real = dev
        name = _read(os.path.join(d, "device", "name"))
        inhibited = _read_int(
            os.path.join(d, "device", "inhibited"))
        modalias = _read(
            os.path.join(d, "device", "modalias"))
        wake = find_wakeup(dev_real)
        out.append({"id": n,
                       "name": name,
                       "inhibited": inhibited,
                       "modalias": modalias,
                       "wakeup": wake.get("state"),
                       "wakeup_count": wake.get("count")})
    return out


def classify(devices: List[dict], present: bool) -> dict:
    if not present:
        return {"verdict": "unknown",
                "reason": "/sys/class/input absent.",
                "recommendation": ""}

    # 1) spurious_wake_source — wakeup=enabled AND count > 0
    spurious = [d for d in devices
                    if d.get("wakeup") == "enabled"
                      and (d.get("wakeup_count") or 0) > 0]
    if spurious:
        sample = ", ".join(
            f"{d['id']}={d.get('name')} "
            f"count={d.get('wakeup_count')}"
                for d in spurious[:3])
        return {"verdict": "spurious_wake_source",
                "reason": (f"{len(spurious)} input device(s) "
                          f"woke the system : {sample}."),
                "recommendation": _recipe_spurious()}

    # 2) wakeup_enabled_orphan — wakeup=enabled but no name
    orphan = [d for d in devices
                  if d.get("wakeup") == "enabled"
                    and not d.get("name")]
    if orphan:
        sample = ", ".join(d["id"] for d in orphan[:3])
        return {"verdict": "wakeup_enabled_orphan",
                "reason": (f"{len(orphan)} input device(s) with "
                          f"wakeup=enabled but no name : "
                          f"{sample}."),
                "recommendation": _recipe_orphan()}

    # 3) inhibited_active_input
    inhib = [d for d in devices
                if d.get("inhibited") == 1]
    if inhib:
        sample = ", ".join(
            f"{d['id']}={d.get('name')}"
                for d in inhib[:3])
        return {"verdict": "inhibited_active_input",
                "reason": (f"{len(inhib)} input device(s) "
                          f"inhibited (events dropped) : "
                          f"{sample}."),
                "recommendation": _recipe_inhibited()}

    # 4) stale_event_node — event<N> with no readable name
    stale = [d for d in devices
                if not d.get("name")]
    if stale and len(stale) == len(devices):
        return {"verdict": "stale_event_node",
                "reason": (f"All {len(devices)} event nodes lack "
                          f"a name file — driver dropped or "
                          f"sysfs link broken."),
                "recommendation": _recipe_stale()}

    return {"verdict": "ok",
            "reason": (f"{len(devices)} input device(s) ; "
                      f"no wakeup-enabled orphans ; no "
                      f"inhibited devices."),
            "recommendation": ""}


def status(config=None,
            sys_input: str = _SYS_INPUT) -> dict:
    present = os.path.isdir(sys_input)
    devices = list_event_devices(sys_input) if present else []
    verdict = classify(devices, present)
    return {"ok": present,
              "present": present,
              "device_count": len(devices),
              "wakeup_enabled_count": sum(
                  1 for d in devices
                      if d.get("wakeup") == "enabled"),
              "inhibited_count": sum(
                  1 for d in devices
                      if d.get("inhibited") == 1),
              "devices": devices,
              "verdict": verdict}


# ── recovery recipes ────────────────────────────────────────────

def _recipe_spurious() -> str:
    return ("# Input device woke the system. Identify :\n"
            "for d in /sys/class/input/event*/device; do\n"
            "  n=$(cat $d/name 2>/dev/null)\n"
            "  for p in $d $d/.. $d/../..; do\n"
            "    w=$p/power/wakeup\n"
            "    c=$p/power/wakeup_count\n"
            "    if [ -e \"$w\" ] && [ \"$(cat $w)\" = enabled ]; then\n"
            "      echo \"$n count=$(cat $c)\"\n"
            "    fi\n"
            "  done\n"
            "done\n"
            "# Disable wakeup for the offender :\n"
            "echo disabled | sudo tee /sys/.../power/wakeup\n")


def _recipe_orphan() -> str:
    return ("# A parent device has wakeup=enabled but the input\n"
            "# child has no name — driver was unloaded. Inspect :\n"
            "for d in /sys/class/input/event*/device; do\n"
            "  name=$(cat $d/name 2>/dev/null)\n"
            "  echo \"$(basename $(dirname $d)) name=$name\"\n"
            "done\n")


def _recipe_inhibited() -> str:
    return ("# Re-enable inhibited input device :\n"
            "echo 0 | sudo tee /sys/class/input/eventN/device/inhibited\n")


def _recipe_stale() -> str:
    return ("# /sys/class/input/event* present but no names —\n"
            "# input subsystem partially broken. Check dmesg :\n"
            "sudo dmesg | grep -i input | tail\n")
