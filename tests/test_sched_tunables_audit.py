"""Tests for modules/sched_tunables_audit.py — R&D #79.4."""
from __future__ import annotations

import pytest

from gpu_dashboard.modules import sched_tunables_audit as mod


def _mk_sched_root(tmp_path, **values):
    """Each kwarg becomes /proc/sys/kernel/<name> file."""
    d = tmp_path / "kernel"
    d.mkdir(parents=True, exist_ok=True)
    for k, v in values.items():
        if v is None:
            continue
        (d / k).write_text(f"{v}\n")
    return str(d)


# --- read_tunables ---------------------------------------------

def test_read_missing(tmp_path):
    out = mod.read_tunables(str(tmp_path / "nope"))
    assert all(v is None for v in out.values())


def test_read_populated(tmp_path):
    r = _mk_sched_root(tmp_path,
                          sched_rt_runtime_us=950000,
                          sched_autogroup_enabled=1)
    out = mod.read_tunables(r)
    assert out["sched_rt_runtime_us"] == 950000
    assert out["sched_autogroup_enabled"] == 1
    assert out["sched_energy_aware"] is None


def test_read_empty_value(tmp_path):
    # sched_energy_aware on some kernels is empty
    d = tmp_path / "kernel"
    d.mkdir()
    (d / "sched_energy_aware").write_text("\n")
    out = mod.read_tunables(str(d))
    assert out["sched_energy_aware"] is None


# --- read_features ---------------------------------------------

def test_read_features_missing(tmp_path):
    assert mod.read_features(
        str(tmp_path / "nope")) is None


def test_read_features_present(tmp_path):
    d = tmp_path / "debug"
    d.mkdir()
    (d / "features").write_text(
        "GENTLE_FAIR_SLEEPERS NO_LB_TIP_AVG_TOTAL CACHE_HOT_BUDDY\n")
    out = mod.read_features(str(d))
    assert "GENTLE_FAIR_SLEEPERS" in out
    assert "NO_LB_TIP_AVG_TOTAL" in out


# --- classify --------------------------------------------------

def test_classify_unknown_empty():
    v = mod.classify({k: None for k in mod._TUNABLES}, None)
    assert v["verdict"] == "unknown"


def _ok_tun():
    return {
        "sched_rt_runtime_us": 950000,
        "sched_rt_period_us": 1000000,
        "sched_autogroup_enabled": 1,
        "sched_schedstats": 1,
        "sched_cfs_bandwidth_slice_us": 5000,
        "sched_rr_timeslice_ms": 100,
        "sched_util_clamp_max": 1024,
        "sched_util_clamp_min": 0,
        "sched_energy_aware": None,
        "sched_child_runs_first": 0,
        "sched_deadline_period_max_us": None,
        "sched_deadline_period_min_us": None,
        "sched_util_clamp_min_rt_default": 1024,
    }


def test_classify_ok():
    v = mod.classify(_ok_tun(), ["GENTLE_FAIR_SLEEPERS"])
    assert v["verdict"] == "ok"


def test_classify_unbounded_rt_runtime():
    t = _ok_tun()
    t["sched_rt_runtime_us"] = -1
    v = mod.classify(t, None)
    assert v["verdict"] == "unbounded_rt_runtime"


def test_classify_autogroup_off():
    t = _ok_tun()
    t["sched_autogroup_enabled"] = 0
    v = mod.classify(t, None)
    assert v["verdict"] == "autogroup_off"


def test_classify_rt_ratio_low():
    t = _ok_tun()
    t["sched_rt_runtime_us"] = 500000  # 50 %
    t["sched_rt_period_us"] = 1000000
    v = mod.classify(t, None)
    assert v["verdict"] == "rt_ratio_low"


def test_classify_rt_ratio_at_floor_ok():
    t = _ok_tun()
    t["sched_rt_runtime_us"] = 800000  # 80 %
    t["sched_rt_period_us"] = 1000000
    v = mod.classify(t, None)
    assert v["verdict"] == "ok"


def test_classify_schedstats_off():
    t = _ok_tun()
    t["sched_schedstats"] = 0
    v = mod.classify(t, None)
    assert v["verdict"] == "schedstats_off"


# Priority : unbounded > autogroup > ratio > schedstats
def test_priority_unbounded_over_autogroup():
    t = _ok_tun()
    t["sched_rt_runtime_us"] = -1
    t["sched_autogroup_enabled"] = 0
    v = mod.classify(t, None)
    assert v["verdict"] == "unbounded_rt_runtime"


def test_priority_autogroup_over_ratio():
    t = _ok_tun()
    t["sched_autogroup_enabled"] = 0
    t["sched_rt_runtime_us"] = 500000  # 50 %
    v = mod.classify(t, None)
    assert v["verdict"] == "autogroup_off"


def test_priority_ratio_over_schedstats():
    t = _ok_tun()
    t["sched_rt_runtime_us"] = 500000
    t["sched_schedstats"] = 0
    v = mod.classify(t, None)
    assert v["verdict"] == "rt_ratio_low"


# --- status integration ----------------------------------------

def test_status_unknown(tmp_path):
    out = mod.status(None,
                       str(tmp_path / "nope"),
                       str(tmp_path / "nope_debug"))
    assert out["ok"] is False
    assert out["verdict"]["verdict"] == "unknown"


def test_status_ok(tmp_path):
    r = _mk_sched_root(tmp_path,
                          sched_rt_runtime_us=950000,
                          sched_rt_period_us=1000000,
                          sched_autogroup_enabled=1,
                          sched_schedstats=1)
    out = mod.status(None, r, str(tmp_path / "nope_debug"))
    assert out["ok"] is True
    assert out["verdict"]["verdict"] == "ok"
    assert out["features_readable"] is False


def test_status_unbounded_rt(tmp_path):
    r = _mk_sched_root(tmp_path,
                          sched_rt_runtime_us=-1,
                          sched_rt_period_us=1000000,
                          sched_autogroup_enabled=1,
                          sched_schedstats=1)
    out = mod.status(None, r, str(tmp_path / "nope_debug"))
    assert out["ok"] is False
    assert out["verdict"]["verdict"] == "unbounded_rt_runtime"
