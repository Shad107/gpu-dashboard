"""Tests for modules/input_device_audit.py — R&D #76.4."""
from __future__ import annotations

import os

import pytest

from gpu_dashboard.modules import input_device_audit as mod


def _mk_event(root, name, *, dev_name="Test", inhibited=0,
                  modalias="input:test"):
    """Build /sys/class/input/eventN/device/{name,inhibited,modalias}"""
    d = root / name / "device"
    d.mkdir(parents=True, exist_ok=True)
    (d / "name").write_text(dev_name + "\n")
    (d / "inhibited").write_text(f"{inhibited}\n")
    (d / "modalias").write_text(modalias + "\n")


def _mk_wakeup(root, name, *, state="disabled", count=0):
    """Inject power/wakeup at the device level."""
    p = root / name / "device" / "power"
    p.mkdir(parents=True, exist_ok=True)
    (p / "wakeup").write_text(state + "\n")
    (p / "wakeup_count").write_text(f"{count}\n")


# --- find_wakeup -----------------------------------------------

def test_find_wakeup_missing(tmp_path):
    out = mod.find_wakeup(str(tmp_path / "nope"))
    assert out == {"state": None, "count": None}


def test_find_wakeup_direct(tmp_path):
    p = tmp_path / "dev"
    (p / "power").mkdir(parents=True)
    (p / "power" / "wakeup").write_text("enabled\n")
    (p / "power" / "wakeup_count").write_text("3\n")
    out = mod.find_wakeup(str(p))
    assert out == {"state": "enabled", "count": 3}


def test_find_wakeup_up_one_level(tmp_path):
    p = tmp_path / "parent" / "child"
    p.mkdir(parents=True)
    (tmp_path / "parent" / "power").mkdir()
    (tmp_path / "parent" / "power" / "wakeup").write_text(
        "enabled\n")
    out = mod.find_wakeup(str(p))
    assert out["state"] == "enabled"


# --- list_event_devices ----------------------------------------

def test_list_missing(tmp_path):
    assert mod.list_event_devices(str(tmp_path / "nope")) == []


def test_list_event_basic(tmp_path):
    _mk_event(tmp_path, "event0", dev_name="Power Button")
    _mk_event(tmp_path, "event1", dev_name="USB Keyboard",
                  inhibited=1)
    out = mod.list_event_devices(str(tmp_path))
    assert len(out) == 2
    by_id = {e["id"]: e for e in out}
    assert by_id["event0"]["name"] == "Power Button"
    assert by_id["event1"]["inhibited"] == 1


def test_list_event_with_wakeup(tmp_path):
    _mk_event(tmp_path, "event0", dev_name="USB Keyboard")
    _mk_wakeup(tmp_path, "event0", state="enabled", count=5)
    out = mod.list_event_devices(str(tmp_path))
    assert out[0]["wakeup"] == "enabled"
    assert out[0]["wakeup_count"] == 5


# --- classify ---------------------------------------------------

def test_classify_unknown():
    v = mod.classify([], False)
    assert v["verdict"] == "unknown"


def test_classify_ok():
    v = mod.classify(
        [{"id": "event0", "name": "PowerBtn",
            "inhibited": 0, "modalias": "x",
            "wakeup": None, "wakeup_count": None}],
        True)
    assert v["verdict"] == "ok"


def test_classify_spurious_wake():
    v = mod.classify(
        [{"id": "event0", "name": "USB Keyboard",
            "inhibited": 0, "modalias": "usb",
            "wakeup": "enabled", "wakeup_count": 3}],
        True)
    assert v["verdict"] == "spurious_wake_source"


def test_classify_wakeup_orphan():
    v = mod.classify(
        [{"id": "event0", "name": None,
            "inhibited": 0, "modalias": "x",
            "wakeup": "enabled", "wakeup_count": 0}],
        True)
    assert v["verdict"] == "wakeup_enabled_orphan"


def test_classify_inhibited():
    v = mod.classify(
        [{"id": "event0", "name": "Kbd",
            "inhibited": 1, "modalias": "x",
            "wakeup": None, "wakeup_count": None}],
        True)
    assert v["verdict"] == "inhibited_active_input"


def test_classify_stale_event_node():
    v = mod.classify(
        [{"id": "event0", "name": None,
            "inhibited": None, "modalias": None,
            "wakeup": None, "wakeup_count": None}],
        True)
    assert v["verdict"] == "stale_event_node"


# Priority : spurious > orphan > inhibited > stale
def test_priority_spurious_over_orphan():
    v = mod.classify(
        [{"id": "event0", "name": None,
            "inhibited": 0, "modalias": "x",
            "wakeup": "enabled", "wakeup_count": 5}],
        True)
    # No name + wakeup enabled + count > 0 = spurious wins
    # over orphan
    assert v["verdict"] == "spurious_wake_source"


def test_priority_orphan_over_inhibited():
    v = mod.classify(
        [{"id": "event0", "name": None,
            "inhibited": 1, "modalias": "x",
            "wakeup": "enabled", "wakeup_count": 0},
          {"id": "event1", "name": "Kbd",
            "inhibited": 1, "modalias": "y",
            "wakeup": "disabled",
            "wakeup_count": 0}],
        True)
    assert v["verdict"] == "wakeup_enabled_orphan"


# --- status integration ----------------------------------------

def test_status_unknown(tmp_path):
    out = mod.status(None, str(tmp_path / "nope"))
    assert out["ok"] is False
    assert out["verdict"]["verdict"] == "unknown"


def test_status_ok_synthetic(tmp_path):
    _mk_event(tmp_path, "event0", dev_name="Power Button")
    _mk_event(tmp_path, "event1", dev_name="Keyboard")
    out = mod.status(None, str(tmp_path))
    assert out["ok"] is True
    assert out["device_count"] == 2
    assert out["verdict"]["verdict"] == "ok"


def test_status_inhibited_synthetic(tmp_path):
    _mk_event(tmp_path, "event0", dev_name="Keyboard",
                  inhibited=1)
    out = mod.status(None, str(tmp_path))
    assert out["verdict"]["verdict"] == "inhibited_active_input"
