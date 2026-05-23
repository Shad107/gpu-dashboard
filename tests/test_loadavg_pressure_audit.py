"""Tests for modules/loadavg_pressure_audit.py — R&D #57.4."""
from __future__ import annotations

import pytest

from gpu_dashboard.modules import loadavg_pressure_audit as mod


# --- parse_loadavg ----------------------------------------------

def test_parse_loadavg_basic():
    out = mod.parse_loadavg("0.49 0.59 0.72 1/1742 211935\n")
    assert out == (0.49, 0.59, 0.72)


def test_parse_loadavg_empty():
    assert mod.parse_loadavg("") == (None, None, None)
    assert mod.parse_loadavg(None) == (None, None, None)


# --- parse_stat -------------------------------------------------

def test_parse_stat_basic():
    text = ("cpu 1 2 3 4\n"
              "procs_running 5\n"
              "procs_blocked 2\n")
    out = mod.parse_stat(text)
    assert out["procs_running"] == 5
    assert out["procs_blocked"] == 2


def test_parse_stat_partial():
    text = ("cpu 1 2 3 4\n"
              "procs_running 5\n")
    out = mod.parse_stat(text)
    assert out["procs_running"] == 5
    assert "procs_blocked" not in out


# --- count_cpus -------------------------------------------------

def test_count_cpus():
    text = ("processor       : 0\nflags : ...\n"
              "processor       : 1\nflags : ...\n"
              "processor       : 2\nflags : ...\n")
    assert mod.count_cpus(text) == 3


def test_count_cpus_empty():
    assert mod.count_cpus("") == 0
    assert mod.count_cpus(None) == 0


# --- classify ---------------------------------------------------

def _la(la1=1.0, la5=1.0, la15=1.0):
    return (la1, la5, la15)


def _stat(running=2, blocked=0):
    return {"procs_running": running, "procs_blocked": blocked}


def test_classify_unknown():
    v = mod.classify(_la(None, None, None), {}, 0,
                       950000, 1000000)
    assert v["verdict"] == "unknown"


def test_classify_ok():
    v = mod.classify(_la(0.5, 0.5, 0.5), _stat(), 12,
                       950000, 1000000)
    assert v["verdict"] == "ok"


def test_classify_rt_throttle_disabled_minus_one():
    v = mod.classify(_la(0.5, 0.5, 0.5), _stat(), 12,
                       -1, 1000000)
    assert v["verdict"] == "rt_throttle_disabled"


def test_classify_rt_throttle_disabled_above_period():
    # rt_runtime > rt_period also disables
    v = mod.classify(_la(0.5, 0.5, 0.5), _stat(), 12,
                       2_000_000, 1_000_000)
    assert v["verdict"] == "rt_throttle_disabled"


def test_classify_d_state_storm():
    v = mod.classify(_la(0.5, 0.5, 0.5),
                       _stat(blocked=5), 12, 950000, 1000000)
    assert v["verdict"] == "D_state_storm"


def test_classify_overcommitted():
    # la = 30 on 12 CPUs : 30 > 2 * 12 = 24
    v = mod.classify(_la(30.0, 25.0, 20.0), _stat(), 12,
                       950000, 1000000)
    assert v["verdict"] == "overcommitted"


def test_classify_priority_rt_wins():
    v = mod.classify(_la(30.0, 25.0, 20.0),
                       _stat(blocked=5), 12, -1, 1000000)
    assert v["verdict"] == "rt_throttle_disabled"


# --- status integration -----------------------------------------

def test_status_unknown(tmp_path):
    out = mod.status(None,
                       str(tmp_path / "nope1"),
                       str(tmp_path / "nope2"),
                       str(tmp_path / "nope3"),
                       str(tmp_path / "nope4"),
                       str(tmp_path / "nope5"))
    assert out["ok"] is False
    assert out["verdict"]["verdict"] == "unknown"


def test_status_live_like(tmp_path):
    la = tmp_path / "loadavg"
    la.write_text("0.5 0.6 0.7 1/100 1234\n")
    st = tmp_path / "stat"
    st.write_text("cpu 1 2 3 4\nprocs_running 2\n"
                     "procs_blocked 0\n")
    ci = tmp_path / "cpuinfo"
    ci.write_text("processor : 0\n" * 1 + "processor : 1\n")
    rtr = tmp_path / "rt_runtime"
    rtr.write_text("950000\n")
    rtp = tmp_path / "rt_period"
    rtp.write_text("1000000\n")
    out = mod.status(None, str(la), str(st), str(ci),
                       str(rtr), str(rtp))
    assert out["ok"] is True
    assert out["loadavg_1m"] == 0.5
    assert out["nr_cpus"] == 2
    assert out["verdict"]["verdict"] == "ok"
