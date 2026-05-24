"""Tests for modules/tracing_events_enable_audit.py — R&D #72.3."""
from __future__ import annotations

import pytest

from gpu_dashboard.modules import tracing_events_enable_audit as mod


def _mk_subsys(root, name, *, agg_enable=None,
                   events=None):
    """Build /sys/kernel/tracing/events/<name>/[events][/enable]."""
    d = root / name
    d.mkdir(parents=True, exist_ok=True)
    if agg_enable is not None:
        (d / "enable").write_text(str(agg_enable) + "\n")
    for evt_name, evt_enable in (events or {}).items():
        evt = d / evt_name
        evt.mkdir(parents=True, exist_ok=True)
        (evt / "enable").write_text(str(evt_enable) + "\n")


# --- list_subsystems -------------------------------------------

def test_list_subsystems_missing(tmp_path):
    assert mod.list_subsystems(str(tmp_path / "nope")) == []


def test_list_subsystems(tmp_path):
    _mk_subsys(tmp_path, "sched", agg_enable=0)
    _mk_subsys(tmp_path, "drm", agg_enable=0)
    out = mod.list_subsystems(str(tmp_path))
    assert out == ["drm", "sched"]


# --- scan_enabled ----------------------------------------------

def test_scan_empty(tmp_path):
    out = mod.scan_enabled(str(tmp_path))
    assert out["total_enabled"] == 0
    assert out["readable"] is False


def test_scan_subsys_all_off(tmp_path):
    # Aggregate enable=0 means we can skip walking children
    _mk_subsys(tmp_path, "sched", agg_enable=0,
                  events={"sched_switch": 0})
    out = mod.scan_enabled(str(tmp_path))
    assert out["readable"] is True
    assert out["total_enabled"] == 0


def test_scan_subsys_with_enabled_event(tmp_path):
    # Aggregate enable=X means children disagree → walk
    _mk_subsys(tmp_path, "drm", agg_enable="X",
                  events={"drm_vblank_event": 1,
                            "drm_other": 0})
    out = mod.scan_enabled(str(tmp_path))
    assert out["total_enabled"] == 1
    assert out["enabled_by_subsys"] == {
        "drm": ["drm_vblank_event"]}


def test_scan_subsys_all_on(tmp_path):
    # Aggregate enable=1 means all children on → walk to list
    _mk_subsys(tmp_path, "irq", agg_enable=1,
                  events={"irq_handler_entry": 1,
                            "irq_handler_exit": 1})
    out = mod.scan_enabled(str(tmp_path))
    assert out["total_enabled"] == 2


def test_scan_eacces_counted(tmp_path):
    # No enable file → can't read → counted as eacces
    d = tmp_path / "sched"; d.mkdir()
    out = mod.scan_enabled(str(tmp_path))
    assert out["eacces_count"] == 1
    assert out["readable"] is False


# --- classify ---------------------------------------------------

def _scan(by_subsys=None, readable=True, total=0):
    by_subsys = by_subsys or {}
    return {"readable": readable,
              "enabled_by_subsys": by_subsys,
              "total_enabled": total or sum(
                  len(v) for v in by_subsys.values()),
              "eacces_count": 0}


def test_classify_unknown_path_missing():
    v = mod.classify(False, [], _scan())
    assert v["verdict"] == "unknown"


def test_classify_unknown_no_subsystems():
    v = mod.classify(True, [], _scan())
    assert v["verdict"] == "unknown"


def test_classify_requires_root():
    v = mod.classify(True, ["sched", "drm"],
                          _scan(readable=False))
    assert v["verdict"] == "requires_root"


def test_classify_ok():
    v = mod.classify(True, ["sched", "drm"], _scan())
    assert v["verdict"] == "ok"


def test_classify_gpu_event_stuck_on():
    v = mod.classify(True, ["sched", "drm"],
                          _scan({"drm": ["drm_vblank_event"]}))
    assert v["verdict"] == "gpu_event_stuck_on"


def test_classify_many_subsys_enabled():
    by = {s: ["x"] for s in ("sched", "irq", "power",
                                          "block", "net")}
    v = mod.classify(True, list(by.keys()), _scan(by))
    assert v["verdict"] == "many_subsys_enabled"


def test_classify_single_evt_enabled():
    v = mod.classify(True, ["sched"],
                          _scan({"sched": ["sched_switch"]}))
    assert v["verdict"] == "single_evt_enabled"


# Priority : gpu > many > single
def test_priority_gpu_over_many():
    by = {s: ["x"] for s in
              ("sched", "irq", "power", "block", "drm")}
    v = mod.classify(True, list(by.keys()), _scan(by))
    assert v["verdict"] == "gpu_event_stuck_on"


def test_priority_many_over_single():
    by = {s: ["x"] for s in
              ("sched", "irq", "power", "block", "net")}
    v = mod.classify(True, list(by.keys()), _scan(by))
    assert v["verdict"] == "many_subsys_enabled"


# --- status integration -----------------------------------------

def test_status_unknown(tmp_path):
    out = mod.status(None, str(tmp_path / "nope"))
    assert out["ok"] is False
    assert out["verdict"]["verdict"] == "unknown"


def test_status_ok_synthetic(tmp_path):
    _mk_subsys(tmp_path, "sched", agg_enable=0)
    _mk_subsys(tmp_path, "drm", agg_enable=0)
    out = mod.status(None, str(tmp_path))
    assert out["ok"] is True
    assert out["subsystem_count"] == 2
    assert out["gpu_subsystems_present"] == ["drm"]
    assert out["verdict"]["verdict"] == "ok"


def test_status_gpu_stuck_synthetic(tmp_path):
    _mk_subsys(tmp_path, "drm", agg_enable="X",
                  events={"drm_vblank_event": 1})
    out = mod.status(None, str(tmp_path))
    assert out["verdict"]["verdict"] == "gpu_event_stuck_on"
