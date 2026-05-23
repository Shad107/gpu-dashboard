"""Tests for modules/cpufreq_residency_audit.py — R&D #65.2."""
from __future__ import annotations

import pytest

from gpu_dashboard.modules import cpufreq_residency_audit as mod


def _mk_stats(root, cpu, *, time_in_state, total_trans=100):
    d = root / f"cpu{cpu}" / "cpufreq" / "stats"
    d.mkdir(parents=True, exist_ok=True)
    (d / "time_in_state").write_text(time_in_state)
    (d / "total_trans").write_text(f"{total_trans}\n")


# --- parse_time_in_state ----------------------------------------

def test_parse_tis():
    text = ("400000 50\n"
              "800000 200\n"
              "1600000 1000\n")
    out = mod.parse_time_in_state(text)
    assert out == [(400000, 50), (800000, 200),
                       (1600000, 1000)]


def test_parse_tis_empty():
    assert mod.parse_time_in_state("") == []
    assert mod.parse_time_in_state(None) == []


# --- list_cpu_stats ---------------------------------------------

def test_list_cpu_stats_missing(tmp_path):
    assert mod.list_cpu_stats(str(tmp_path / "nope")) == {}


def test_list_cpu_stats(tmp_path):
    _mk_stats(tmp_path, 0,
                 time_in_state="400000 50\n1600000 1000\n",
                 total_trans=100)
    _mk_stats(tmp_path, 1,
                 time_in_state="400000 200\n1600000 800\n",
                 total_trans=200)
    out = mod.list_cpu_stats(str(tmp_path))
    assert set(out.keys()) == {0, 1}
    assert out[0]["total_trans"] == 100


# --- classify ---------------------------------------------------

def _stats(time_in_state, total_trans=100):
    return {"time_in_state": time_in_state,
              "total_trans": total_trans}


def test_classify_unknown():
    v = mod.classify({})
    assert v["verdict"] == "unknown"


def test_classify_ok():
    v = mod.classify({
        0: _stats([(400000, 100), (800000, 400),
                     (1600000, 500)])})
    assert v["verdict"] == "ok"


def test_classify_pinned_min():
    v = mod.classify({
        0: _stats([(400000, 9999), (800000, 1), (1600000, 0)])})
    assert v["verdict"] == "pinned_at_min"


def test_classify_pinned_max():
    v = mod.classify({
        0: _stats([(400000, 0), (800000, 1), (1600000, 9999)])})
    assert v["verdict"] == "pinned_at_max"


def test_classify_transition_storm():
    v = mod.classify({
        0: _stats([(400000, 100), (1600000, 100)],
                     total_trans=200_000)})
    assert v["verdict"] == "transition_storm"


def test_classify_boost_unreachable():
    v = mod.classify({
        0: _stats([(400000, 100), (1600000, 100)]),
        1: _stats([(400000, 100), (800000, 50)])})
    assert v["verdict"] == "boost_unreachable"


# --- status integration -----------------------------------------

def test_status_unknown(tmp_path):
    out = mod.status(None, str(tmp_path / "nope"))
    assert out["ok"] is False
    assert out["verdict"]["verdict"] == "unknown"


def test_status_live_like(tmp_path):
    _mk_stats(tmp_path, 0,
                 time_in_state="400000 100\n800000 300\n"
                                  "1600000 600\n",
                 total_trans=50)
    _mk_stats(tmp_path, 1,
                 time_in_state="400000 100\n800000 300\n"
                                  "1600000 600\n",
                 total_trans=50)
    out = mod.status(None, str(tmp_path))
    assert out["ok"] is True
    assert out["cpu_count"] == 2
    assert out["verdict"]["verdict"] == "ok"
