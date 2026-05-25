"""Tests for modules/cgroup_v2_memory_peak_audit.py R&D #93.2."""
from __future__ import annotations

import pytest

from gpu_dashboard.modules import (
    cgroup_v2_memory_peak_audit as mod)


def _mk_root(tmp_path, *, controllers="cpu memory pids"):
    d = tmp_path / "cgroup"
    d.mkdir(parents=True, exist_ok=True)
    (d / "cgroup.controllers").write_text(controllers + "\n")
    return str(d)


def _mk_leaf(tmp_path, path, *, peak="1000000",
              max_v="max", high="max",
              swap_peak="0",
              events_local="high 0\nmax 0\noom 0\noom_kill 0\n"):
    d = tmp_path / "cgroup" / path
    d.mkdir(parents=True, exist_ok=True)
    (d / "memory.peak").write_text(peak + "\n")
    (d / "memory.max").write_text(max_v + "\n")
    (d / "memory.high").write_text(high + "\n")
    (d / "memory.swap.peak").write_text(swap_peak + "\n")
    (d / "memory.events.local").write_text(events_local)
    return str(tmp_path / "cgroup")


# --- parse_events_local ----------------------------------------

def test_parse_events_local_empty():
    assert mod.parse_events_local("") == {}
    assert mod.parse_events_local(None) == {}


def test_parse_events_local_typical():
    text = "high 5\nmax 0\noom 0\noom_kill 0\n"
    out = mod.parse_events_local(text)
    assert out["high"] == 5
    assert out["max"] == 0


# --- _read_int -------------------------------------------------

def test_read_int_max_is_none(tmp_path):
    p = tmp_path / "f"
    p.write_text("max\n")
    assert mod._read_int(str(p)) is None


def test_read_int_numeric(tmp_path):
    p = tmp_path / "f"
    p.write_text("12345\n")
    assert mod._read_int(str(p)) == 12345


# --- controller_present ----------------------------------------

def test_controller_present_missing(tmp_path):
    assert mod.controller_present(
        str(tmp_path / "nope")) is None


def test_controller_present_yes(tmp_path):
    r = _mk_root(tmp_path)
    assert mod.controller_present(r) is True


def test_controller_present_no(tmp_path):
    r = _mk_root(tmp_path, controllers="cpu pids")
    assert mod.controller_present(r) is False


# --- walk_cgroups ----------------------------------------------

def test_walk_cgroups_missing(tmp_path):
    assert mod.walk_cgroups(str(tmp_path / "nope")) == []


def test_walk_cgroups_finds_leaves(tmp_path):
    _mk_root(tmp_path)
    _mk_leaf(tmp_path, "user.slice",
                 peak="1024", max_v="10240")
    _mk_leaf(tmp_path, "system.slice",
                 peak="2048", max_v="max")
    out = mod.walk_cgroups(str(tmp_path / "cgroup"))
    assert len(out) == 2
    by_path = {c["path"]: c for c in out}
    assert by_path["user.slice"]["max"] == 10240
    assert by_path["system.slice"]["max"] is None


# --- classify --------------------------------------------------

def _leaf(*, path="x", peak=100, max_v=None, high=None,
          swap_peak=0, events_local_high=0,
          events_local_max=0):
    return {"path": path, "peak": peak, "max": max_v,
            "high": high, "swap_peak": swap_peak,
            "events_local_high": events_local_high,
            "events_local_max": events_local_max}


def test_classify_requires_root():
    v = mod.classify(None, [])
    assert v["verdict"] == "requires_root"


def test_classify_unknown_no_memcg():
    v = mod.classify(False, [])
    assert v["verdict"] == "unknown"


def test_classify_ok_no_cgroups():
    v = mod.classify(True, [])
    assert v["verdict"] == "ok"


def test_classify_ok_no_max_set():
    v = mod.classify(True, [_leaf(peak=100, max_v=None)])
    assert v["verdict"] == "ok"


def test_classify_peak_at_max_err():
    v = mod.classify(True, [_leaf(
        path="user.slice/leaf",
        peak=980, max_v=1000)])
    assert v["verdict"] == "peak_at_max"


def test_classify_peak_at_max_below_threshold_ok():
    v = mod.classify(True, [_leaf(
        peak=950, max_v=1000)])
    assert v["verdict"] == "ok"


def test_classify_peak_at_high_throttling():
    v = mod.classify(True, [_leaf(
        path="user.slice",
        peak=900, high=800,
        events_local_high=3)])
    assert v["verdict"] == "peak_at_high_throttling"


def test_classify_high_events_but_no_throttle_yet():
    # events.local says high triggered before but current peak
    # is below high — could be after a recovery
    v = mod.classify(True, [_leaf(
        peak=400, high=800,
        events_local_high=3)])
    assert v["verdict"] == "ok"


def test_classify_swap_peak_active():
    v = mod.classify(True, [_leaf(
        path="user.slice/leaf",
        swap_peak=1024)])
    assert v["verdict"] == "swap_peak_active"


# Priority : peak_at_max > peak_at_high > swap_peak
def test_priority_peak_max_over_swap():
    v = mod.classify(True, [
        _leaf(peak=980, max_v=1000),
        _leaf(swap_peak=999),
    ])
    assert v["verdict"] == "peak_at_max"


def test_priority_high_throttle_over_swap():
    v = mod.classify(True, [
        _leaf(peak=900, high=800,
              events_local_high=1),
        _leaf(swap_peak=999),
    ])
    assert v["verdict"] == "peak_at_high_throttling"


# --- status integration ----------------------------------------

def test_status_unknown(tmp_path):
    out = mod.status(None, str(tmp_path / "nope"))
    assert out["verdict"]["verdict"] == "requires_root"


def test_status_ok_synthetic(tmp_path):
    _mk_root(tmp_path)
    _mk_leaf(tmp_path, "user.slice",
                 peak="100", max_v="max")
    out = mod.status(None, str(tmp_path / "cgroup"))
    assert out["verdict"]["verdict"] == "ok"
    assert out["cgroup_count"] >= 1


def test_status_peak_at_max_synthetic(tmp_path):
    _mk_root(tmp_path)
    _mk_leaf(tmp_path, "user.slice/leaf",
                 peak="990", max_v="1000")
    out = mod.status(None, str(tmp_path / "cgroup"))
    assert out["verdict"]["verdict"] == "peak_at_max"
    assert out["ok"] is False
