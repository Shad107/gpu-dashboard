"""Tests for modules/cgroup_pids_controller_audit.py — R&D #91.1."""
from __future__ import annotations

import pytest

from gpu_dashboard.modules import (
    cgroup_pids_controller_audit as mod)


def _mk_cgroup(tmp_path, path, *, pids_max="max",
                pids_current="0", pids_events="max 0\n"):
    d = tmp_path / "cgroup" / path
    d.mkdir(parents=True, exist_ok=True)
    (d / "pids.max").write_text(pids_max + "\n")
    (d / "pids.current").write_text(pids_current + "\n")
    (d / "pids.events").write_text(pids_events)
    return str(tmp_path / "cgroup")


def _mk_root(tmp_path, *, controllers="cpuset cpu pids memory"):
    d = tmp_path / "cgroup"
    d.mkdir(parents=True, exist_ok=True)
    (d / "cgroup.controllers").write_text(controllers + "\n")
    return str(d)


# --- _parse_pids_max -------------------------------------------

def test_parse_pids_max_unlimited():
    assert mod._parse_pids_max("max") is None


def test_parse_pids_max_integer():
    assert mod._parse_pids_max("1000") == 1000


def test_parse_pids_max_garbage():
    assert mod._parse_pids_max("zz") is None
    assert mod._parse_pids_max("") is None


# --- _parse_pids_events ----------------------------------------

def test_parse_pids_events_zero():
    assert mod._parse_pids_events("max 0\nmax.imposed 0\n") == 0


def test_parse_pids_events_hit():
    assert mod._parse_pids_events(
        "max 5\nmax.imposed 5\n") == 5


def test_parse_pids_events_empty():
    assert mod._parse_pids_events("") == 0


# --- controller_present ----------------------------------------

def test_controller_present_missing(tmp_path):
    assert mod.controller_present(str(tmp_path / "nope")) is None


def test_controller_present_yes(tmp_path):
    r = _mk_root(tmp_path)
    assert mod.controller_present(r) is True


def test_controller_present_no(tmp_path):
    r = _mk_root(tmp_path, controllers="cpuset cpu memory")
    assert mod.controller_present(r) is False


# --- walk_cgroups ----------------------------------------------

def test_walk_cgroups_skips_unlimited(tmp_path):
    _mk_root(tmp_path)
    _mk_cgroup(tmp_path, "user.slice",
                  pids_max="max", pids_current="50")
    out = mod.walk_cgroups(str(tmp_path / "cgroup"))
    assert out == []


def test_walk_cgroups_finds_numeric(tmp_path):
    _mk_root(tmp_path)
    _mk_cgroup(tmp_path, "user.slice/user-1000.slice",
                  pids_max="500", pids_current="50")
    out = mod.walk_cgroups(str(tmp_path / "cgroup"))
    assert len(out) == 1
    assert out[0]["max"] == 500
    assert out[0]["current"] == 50


# --- classify --------------------------------------------------

def test_classify_requires_root():
    v = mod.classify(None, [])
    assert v["verdict"] == "requires_root"


def test_classify_unknown_no_pids_controller():
    v = mod.classify(False, [])
    assert v["verdict"] == "unknown"


def test_classify_ok_no_numeric_caps():
    v = mod.classify(True, [])
    assert v["verdict"] == "ok"


def test_classify_ok_all_healthy():
    v = mod.classify(True, [
        {"path": "user.slice", "max": 1000,
         "current": 100, "max_events": 0},
    ])
    assert v["verdict"] == "ok"


def test_classify_pids_max_hit():
    v = mod.classify(True, [
        {"path": "user.slice", "max": 100,
         "current": 100, "max_events": 5},
    ])
    assert v["verdict"] == "pids_max_hit"
    assert "user.slice" in v["cgroups"]


def test_classify_pids_max_historic():
    v = mod.classify(True, [
        {"path": "user.slice", "max": 1000,
         "current": 100, "max_events": 3},
    ])
    assert v["verdict"] == "pids_max_historic"


def test_classify_pids_near_limit():
    v = mod.classify(True, [
        {"path": "user.slice", "max": 1000,
         "current": 850, "max_events": 0},
    ])
    assert v["verdict"] == "pids_near_limit"


# Priority : max_hit > historic > near_limit
def test_priority_hit_over_historic():
    v = mod.classify(True, [
        {"path": "a", "max": 100, "current": 100,
         "max_events": 0},
        {"path": "b", "max": 1000, "current": 100,
         "max_events": 5},
    ])
    assert v["verdict"] == "pids_max_hit"


def test_priority_historic_over_near():
    v = mod.classify(True, [
        {"path": "a", "max": 1000, "current": 850,
         "max_events": 0},
        {"path": "b", "max": 1000, "current": 100,
         "max_events": 5},
    ])
    assert v["verdict"] == "pids_max_historic"


# --- status integration ----------------------------------------

def test_status_requires_root(tmp_path):
    out = mod.status(None, str(tmp_path / "nope"))
    assert out["verdict"]["verdict"] == "requires_root"


def test_status_unknown_no_pids(tmp_path):
    _mk_root(tmp_path, controllers="cpuset cpu")
    out = mod.status(None, str(tmp_path / "cgroup"))
    assert out["verdict"]["verdict"] == "unknown"


def test_status_ok_synthetic(tmp_path):
    _mk_root(tmp_path)
    _mk_cgroup(tmp_path, "user.slice",
                  pids_max="max", pids_current="100")
    out = mod.status(None, str(tmp_path / "cgroup"))
    assert out["verdict"]["verdict"] == "ok"
    assert out["cgroup_with_cap_count"] == 0


def test_status_hit_synthetic(tmp_path):
    _mk_root(tmp_path)
    _mk_cgroup(tmp_path, "user.slice/leaf",
                  pids_max="100", pids_current="100",
                  pids_events="max 7\nmax.imposed 7\n")
    out = mod.status(None, str(tmp_path / "cgroup"))
    assert out["verdict"]["verdict"] == "pids_max_hit"
    assert out["ok"] is False
