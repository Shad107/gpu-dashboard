"""Module hot_swap — PCIe / DRM hot-swap drift detector (R&D #14.5).

OcuLink + eGPU + datacenter slot transitions can degrade silently :
the kernel re-enumerates the device at PCIe Gen 1 ×4 instead of Gen 4
×16, halves bandwidth, and nothing screams about it. Display cables
intermittently disconnect during gaming. Power states flap from D0
to D3 unexpectedly.

This module snapshots :
  - /sys/bus/pci/devices/<bdf>/{current_link_speed, current_link_width,
                                 max_link_speed, max_link_width, power_state,
                                 class, vendor}
  - /sys/class/drm/<card-*>/status            ('connected' / 'disconnected')

Each call to evaluate() diffs against the previous snapshot and emits
events :
  - link-downgrade        : current speed < max, OR current width < max
  - link-renegotiate      : current speed / width changed between snapshots
  - power-state-change    : D0 ↔ D3 transition
  - drm-disconnect        : a connector flipped to 'disconnected'
  - drm-reconnect         : the reverse

Events go into a bounded ring buffer (max 200) that callers can read.

stdlib only.
"""
from __future__ import annotations

import glob
import json
import os
import re
import threading
import time
from typing import Optional


NAME = "hot_swap"

# NVIDIA vendor + display-controller class prefix
_NVIDIA_VENDOR = "0x10de"
_BUFFER_MAX = 200
_STATE_PATH = "~/.config/gpu-dashboard/hot_swap_state.json"

_lock = threading.Lock()
_events: list = []


def state_path() -> str:
    return os.path.expanduser(_STATE_PATH)


def _read_text(path: str) -> Optional[str]:
    try:
        with open(path) as f:
            return f.read().strip()
    except OSError:
        return None


def list_nvidia_pci_devices() -> list:
    """Return BDFs of NVIDIA GPU devices (vendor 10de + class display-controller).
    Filters out audio companion devices (class 04xx)."""
    out: list = []
    for dev_path in sorted(glob.glob("/sys/bus/pci/devices/*")):
        vendor = _read_text(os.path.join(dev_path, "vendor"))
        if vendor != _NVIDIA_VENDOR:
            continue
        klass = _read_text(os.path.join(dev_path, "class"))
        # Class 0x03xxxx = display controller. Skip audio (0x040300).
        if not klass or not klass.startswith("0x03"):
            continue
        out.append(os.path.basename(dev_path))
    return out


def snapshot_pci() -> dict:
    """Return dict {bdf : {current_link_speed, current_link_width,
    max_link_speed, max_link_width, power_state}} for NVIDIA GPU devices."""
    out: dict = {}
    for bdf in list_nvidia_pci_devices():
        base = f"/sys/bus/pci/devices/{bdf}"
        out[bdf] = {
            "current_link_speed": _read_text(os.path.join(base, "current_link_speed")),
            "current_link_width": _read_text(os.path.join(base, "current_link_width")),
            "max_link_speed":     _read_text(os.path.join(base, "max_link_speed")),
            "max_link_width":     _read_text(os.path.join(base, "max_link_width")),
            "power_state":        _read_text(os.path.join(base, "power_state")),
        }
    return out


def snapshot_drm() -> dict:
    """Return dict {connector_name : 'connected'/'disconnected'/'unknown'} for
    every /sys/class/drm/card*-*/status entry."""
    out: dict = {}
    for status_path in sorted(glob.glob("/sys/class/drm/*/status")):
        name = os.path.basename(os.path.dirname(status_path))
        out[name] = _read_text(status_path) or "unknown"
    return out


def snapshot_all() -> dict:
    return {
        "ts": int(time.time()),
        "pci": snapshot_pci(),
        "drm": snapshot_drm(),
    }


def load_state() -> dict:
    p = state_path()
    if not os.path.exists(p):
        return {}
    try:
        with open(p) as f:
            d = json.load(f)
        return d if isinstance(d, dict) else {}
    except (OSError, json.JSONDecodeError):
        return {}


def save_state(snap: dict) -> None:
    p = state_path()
    os.makedirs(os.path.dirname(p), exist_ok=True)
    with open(p, "w") as f:
        json.dump(snap, f, indent=2)


def _link_width_int(width: Optional[str]) -> Optional[int]:
    """current_link_width is just an int as text. max_link_width too."""
    if not width:
        return None
    try:
        return int(width)
    except ValueError:
        return None


def _link_speed_gts(speed: Optional[str]) -> Optional[float]:
    """current_link_speed is like '8.0 GT/s PCIe'. Extract the leading float."""
    if not speed:
        return None
    m = re.match(r"^([\d.]+)\s*GT/s", speed)
    if m:
        try:
            return float(m.group(1))
        except ValueError:
            return None
    return None


def diff_snapshots(old: dict, new: dict) -> list:
    """Return list of {kind, target, before, after, gpu? } event dicts."""
    events: list = []
    old_pci = old.get("pci", {})
    new_pci = new.get("pci", {})
    old_drm = old.get("drm", {})
    new_drm = new.get("drm", {})

    for bdf, new_st in new_pci.items():
        old_st = old_pci.get(bdf)
        if old_st is None:
            continue
        # Link renegotiate : current_link_speed OR current_link_width changed
        if (old_st.get("current_link_speed") != new_st.get("current_link_speed") or
                old_st.get("current_link_width") != new_st.get("current_link_width")):
            events.append({
                "kind": "link-renegotiate",
                "gpu": bdf,
                "before": {"speed": old_st.get("current_link_speed"),
                           "width": old_st.get("current_link_width")},
                "after":  {"speed": new_st.get("current_link_speed"),
                           "width": new_st.get("current_link_width")},
            })
        # Power state change
        if old_st.get("power_state") != new_st.get("power_state"):
            events.append({
                "kind": "power-state-change",
                "gpu": bdf,
                "before": old_st.get("power_state"),
                "after":  new_st.get("power_state"),
            })
        # Link downgrade : current vs max
        cur_speed = _link_speed_gts(new_st.get("current_link_speed"))
        max_speed = _link_speed_gts(new_st.get("max_link_speed"))
        cur_width = _link_width_int(new_st.get("current_link_width"))
        max_width = _link_width_int(new_st.get("max_link_width"))
        if (cur_speed is not None and max_speed is not None and cur_speed < max_speed) or \
           (cur_width is not None and max_width is not None and cur_width < max_width):
            # Only emit if we didn't already emit this same downgrade in the
            # previous snapshot (avoid spam on every poll)
            prev_cur_speed = _link_speed_gts(old_st.get("current_link_speed"))
            prev_cur_width = _link_width_int(old_st.get("current_link_width"))
            if prev_cur_speed != cur_speed or prev_cur_width != cur_width:
                events.append({
                    "kind": "link-downgrade",
                    "gpu": bdf,
                    "current": {"speed": new_st.get("current_link_speed"),
                                "width": cur_width},
                    "max": {"speed": new_st.get("max_link_speed"),
                            "width": max_width},
                })

    for conn, new_status in new_drm.items():
        old_status = old_drm.get(conn)
        if old_status is None:
            continue
        if old_status == new_status:
            continue
        if new_status == "disconnected":
            events.append({"kind": "drm-disconnect", "target": conn,
                            "before": old_status, "after": new_status})
        elif new_status == "connected":
            events.append({"kind": "drm-reconnect", "target": conn,
                            "before": old_status, "after": new_status})
    return events


def _append_events(new_events: list, ts: int) -> None:
    if not new_events:
        return
    with _lock:
        for e in new_events:
            e["ts"] = ts
            _events.append(e)
        # Bound the buffer
        if len(_events) > _BUFFER_MAX:
            del _events[: len(_events) - _BUFFER_MAX]


def get_events(limit: int = 100) -> list:
    """Newest-first slice of the event ring buffer."""
    with _lock:
        items = list(_events)
    items.reverse()
    return items[:max(0, min(limit, _BUFFER_MAX))]


def evaluate() -> dict:
    """Take a fresh snapshot, diff against the persisted previous one,
    record any events, and return {events, now_snapshot}."""
    now_snap = snapshot_all()
    prev = load_state()
    events = diff_snapshots(prev, now_snap) if prev else []
    _append_events(events, ts=now_snap["ts"])
    save_state(now_snap)
    return {
        "ok": True,
        "ts": now_snap["ts"],
        "new_events": events,
        "gpu_count": len(now_snap.get("pci", {})),
        "drm_connector_count": len(now_snap.get("drm", {})),
    }


def status() -> dict:
    """Top-level snapshot + recent events for the UI."""
    return {
        "ok": True,
        "current": snapshot_all(),
        "events": get_events(100),
        "buffer_max": _BUFFER_MAX,
    }
