"""Tests for modules/cpu_thermal_throttle_counters_audit.py — R&D #77.2."""
from __future__ import annotations

import pytest

from gpu_dashboard.modules import (
    cpu_thermal_throttle_counters_audit as mod
)


def _mk_cpu(root, cpu, **counters):
    d = root / f"cpu{cpu}" / "thermal_throttle"
    d.mkdir(parents=True, exist_ok=True)
    for k, v in counters.items():
        (d / k).write_text(f"{v}\n")


# --- list_cpus -------------------------------------------------

def test_list_cpus_missing(tmp_path):
    assert mod.list_cpus(str(tmp_path / "nope")) == []


def test_list_cpus(tmp_path):
    for c in (0, 1, 2):
        (tmp_path / f"cpu{c}").mkdir()
    (tmp_path / "cpufreq").mkdir()
    out = mod.list_cpus(str(tmp_path))
    assert out == [0, 1, 2]


# --- read_throttle_counters ------------------------------------

def test_read_counters_missing(tmp_path):
    out = mod.read_throttle_counters(str(tmp_path), 0)
    assert all(v is None for v in out.values())


def test_read_counters_populated(tmp_path):
    _mk_cpu(tmp_path, 0,
                core_throttle_count=5,
                core_throttle_max_time_ms=200,
                package_throttle_count=2)
    out = mod.read_throttle_counters(str(tmp_path), 0)
    assert out["core_throttle_count"] == 5
    assert out["package_throttle_count"] == 2


# --- classify ---------------------------------------------------

def _all_zero():
    return {k: 0 for k in mod._KNOBS}


def test_classify_unknown():
    v = mod.classify(False, {})
    assert v["verdict"] == "unknown"


def test_classify_counters_absent():
    counters = {0: {k: None for k in mod._KNOBS},
                  1: {k: None for k in mod._KNOBS}}
    v = mod.classify(True, counters)
    assert v["verdict"] == "counters_absent"


def test_classify_ok_all_zero():
    counters = {0: _all_zero(), 1: _all_zero()}
    v = mod.classify(True, counters)
    assert v["verdict"] == "ok"


def test_classify_package_throttling():
    counters = {0: _all_zero()}
    counters[0]["package_throttle_count"] = 5
    counters[0]["package_throttle_max_time_ms"] = 200
    v = mod.classify(True, counters)
    assert v["verdict"] == "package_throttling_active"


def test_classify_package_count_without_time_skipped():
    # Count > 0 but max_time = 0 → not triggered
    counters = {0: _all_zero()}
    counters[0]["package_throttle_count"] = 1
    counters[0]["package_throttle_max_time_ms"] = 0
    v = mod.classify(True, counters)
    assert v["verdict"] != "package_throttling_active"


def test_classify_core_throttling():
    counters = {0: _all_zero()}
    counters[0]["core_throttle_count"] = 3
    v = mod.classify(True, counters)
    assert v["verdict"] == "core_throttling_active"


def test_classify_power_limit_hit():
    counters = {0: _all_zero()}
    counters[0]["core_power_limit_count"] = 10
    v = mod.classify(True, counters)
    assert v["verdict"] == "power_limit_hit"


# Priority : package > core > power_limit
def test_priority_package_over_core():
    counters = {0: _all_zero()}
    counters[0]["package_throttle_count"] = 5
    counters[0]["package_throttle_max_time_ms"] = 100
    counters[0]["core_throttle_count"] = 10
    v = mod.classify(True, counters)
    assert v["verdict"] == "package_throttling_active"


def test_priority_core_over_power_limit():
    counters = {0: _all_zero()}
    counters[0]["core_throttle_count"] = 1
    counters[0]["core_power_limit_count"] = 10
    v = mod.classify(True, counters)
    assert v["verdict"] == "core_throttling_active"


# --- status integration ----------------------------------------

def test_status_unknown(tmp_path):
    out = mod.status(None, str(tmp_path / "nope"))
    assert out["ok"] is False
    assert out["verdict"]["verdict"] == "unknown"


def test_status_absent_synthetic(tmp_path):
    (tmp_path / "cpu0").mkdir()
    (tmp_path / "cpu1").mkdir()
    out = mod.status(None, str(tmp_path))
    assert out["verdict"]["verdict"] == "counters_absent"


def test_status_ok_synthetic(tmp_path):
    _mk_cpu(tmp_path, 0, **_all_zero())
    _mk_cpu(tmp_path, 1, **_all_zero())
    out = mod.status(None, str(tmp_path))
    assert out["cpu_count"] == 2
    assert out["verdict"]["verdict"] == "ok"


def test_status_throttling_synthetic(tmp_path):
    knobs = _all_zero()
    knobs["package_throttle_count"] = 5
    knobs["package_throttle_max_time_ms"] = 200
    _mk_cpu(tmp_path, 0, **knobs)
    out = mod.status(None, str(tmp_path))
    assert out["verdict"]["verdict"] == "package_throttling_active"
