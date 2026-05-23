"""Tests for modules/devfreq_event_audit.py — R&D #65.4."""
from __future__ import annotations

import pytest

from gpu_dashboard.modules import devfreq_event_audit as mod


def _mk_event(root, idx, *, name="ppmu-dmc0", enable_count=1):
    d = root / f"event{idx}"
    d.mkdir(parents=True, exist_ok=True)
    (d / "name").write_text(name + "\n")
    (d / "enable_count").write_text(f"{enable_count}\n")


def _mk_devfreq(root, name, *, governor="simple_ondemand"):
    d = root / name
    d.mkdir(parents=True, exist_ok=True)
    (d / "governor").write_text(governor + "\n")


# --- list_events / list_devfreq_devices -------------------------

def test_list_events_missing(tmp_path):
    assert mod.list_events(str(tmp_path / "nope")) == []


def test_list_events(tmp_path):
    _mk_event(tmp_path, 0, enable_count=1)
    _mk_event(tmp_path, 1, enable_count=0)
    out = mod.list_events(str(tmp_path))
    assert len(out) == 2


def test_list_devfreq_devices(tmp_path):
    _mk_devfreq(tmp_path, "dmc-bus", governor="simple_ondemand")
    out = mod.list_devfreq_devices(str(tmp_path))
    assert len(out) == 1
    assert out[0]["governor"] == "simple_ondemand"


# --- classify ---------------------------------------------------

def _e(name="ppmu", enable_count=1):
    return {"id": "event0", "name": name,
              "enable_count": enable_count}


def _d(governor="simple_ondemand"):
    return {"id": "dmc-bus", "governor": governor}


def test_classify_unknown():
    v = mod.classify([], [],
                       event_class_present=False,
                       devfreq_class_present=False)
    assert v["verdict"] == "unknown"


def test_classify_class_absent_empty():
    v = mod.classify([], [],
                       event_class_present=True,
                       devfreq_class_present=False)
    assert v["verdict"] == "class_absent"


def test_classify_ok():
    v = mod.classify([_e(enable_count=1)], [_d()],
                       event_class_present=True,
                       devfreq_class_present=True)
    assert v["verdict"] == "ok"


def test_classify_event_disabled_no_ondemand():
    v = mod.classify([_e(enable_count=0)], [_d(governor="powersave")],
                       event_class_present=True,
                       devfreq_class_present=True)
    assert v["verdict"] == "event_disabled"


def test_classify_event_orphaned_governor():
    v = mod.classify([_e(enable_count=0)], [_d()],
                       event_class_present=True,
                       devfreq_class_present=True)
    assert v["verdict"] == "event_orphaned_governor"


# --- status integration -----------------------------------------

def test_status_unknown(tmp_path):
    out = mod.status(None, str(tmp_path / "noevt"),
                       str(tmp_path / "nodev"))
    assert out["ok"] is False
    assert out["verdict"]["verdict"] == "unknown"


def test_status_live_like(tmp_path):
    evt = tmp_path / "evt"
    dev = tmp_path / "dev"
    _mk_event(evt, 0, name="ppmu-dmc0", enable_count=1)
    _mk_devfreq(dev, "dmc-bus")
    out = mod.status(None, str(evt), str(dev))
    assert out["ok"] is True
    assert out["event_count"] == 1
    assert out["verdict"]["verdict"] == "ok"
