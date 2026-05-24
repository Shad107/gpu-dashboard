"""Tests for modules/sched_features_debugfs_audit.py — R&D #85.4."""
from __future__ import annotations

import pytest

from gpu_dashboard.modules import sched_features_debugfs_audit as mod


def _mk_sched(tmp_path, *, features_text=None, **knobs):
    d = tmp_path / "sched"
    d.mkdir(parents=True, exist_ok=True)
    if features_text is not None:
        (d / "features").write_text(features_text + "\n")
    for name, val in knobs.items():
        (d / name).write_text(f"{val}\n")
    return str(d)


# --- parse_features --------------------------------------------

def test_parse_features_empty():
    assert mod.parse_features("") == {}
    assert mod.parse_features(None) == {}


def test_parse_features_basic():
    text = ("GENTLE_FAIR_SLEEPERS NO_NEXT_BUDDY "
              "WAKEUP_PREEMPTION NO_HRTICK START_DEBIT")
    out = mod.parse_features(text)
    assert out["GENTLE_FAIR_SLEEPERS"] is True
    assert out["NEXT_BUDDY"] is False
    assert out["HRTICK"] is False
    assert out["START_DEBIT"] is True


# --- read_state ------------------------------------------------

def test_read_state_unknown_no_debugfs(tmp_path):
    feats, tunings, state = mod.read_state(
        str(tmp_path / "nope_sched"),
        str(tmp_path / "nope_debug"))
    assert state == "unknown"


def test_read_state_requires_root_debugfs_unreadable(
        tmp_path):
    # debugfs exists but no sched dir AND we can read parent
    debug = tmp_path / "debug"
    debug.mkdir()
    # No sched/ subdir → unknown (debugfs readable but no
    # CFS surface)
    feats, tunings, state = mod.read_state(
        str(debug / "sched"), str(debug))
    assert state == "unknown"


def test_read_state_ok(tmp_path):
    sched = _mk_sched(tmp_path,
                          features_text="GENTLE_FAIR_SLEEPERS",
                          latency_ns=6000000)
    feats, tunings, state = mod.read_state(
        sched, str(tmp_path))
    assert state == "ok"
    assert feats["GENTLE_FAIR_SLEEPERS"] is True
    assert tunings["latency_ns"] == 6000000


# --- classify --------------------------------------------------

def test_classify_unknown():
    v = mod.classify(None, {}, "unknown")
    assert v["verdict"] == "unknown"


def test_classify_requires_root():
    v = mod.classify(None, {}, "requires_root")
    assert v["verdict"] == "requires_root"


def test_classify_ok_defaults():
    feats = {"GENTLE_FAIR_SLEEPERS": True,
              "HRTICK": True, "START_DEBIT": True,
              "NEXT_BUDDY": True}
    tunings = {"latency_ns": 6000000,
                 "min_granularity_ns": 750000,
                 "wakeup_granularity_ns": 1000000,
                 "migration_cost_ns": 500000}
    v = mod.classify(feats, tunings, "ok")
    assert v["verdict"] == "ok"


def test_classify_critical_off_hrtick():
    feats = {"HRTICK": False, "START_DEBIT": True}
    v = mod.classify(feats, {}, "ok")
    assert v["verdict"] == "critical_sched_flags_off"


def test_classify_critical_off_start_debit():
    feats = {"HRTICK": True, "START_DEBIT": False}
    v = mod.classify(feats, {}, "ok")
    assert v["verdict"] == "critical_sched_flags_off"


def test_classify_tuning_drifted():
    feats = {"HRTICK": True, "START_DEBIT": True}
    tunings = {"latency_ns": 24000000}  # 6→24ms = 300%
    v = mod.classify(feats, tunings, "ok")
    assert v["verdict"] == "sched_tuning_drifted"
    assert v["knob"] == "latency_ns"


def test_classify_tuning_small_drift_ok():
    feats = {"HRTICK": True, "START_DEBIT": True}
    tunings = {"latency_ns": 7000000}  # 6→7ms = 17% drift
    v = mod.classify(feats, tunings, "ok")
    assert v["verdict"] == "ok"


def test_classify_one_flag_non_default():
    # GENTLE_FAIR_SLEEPERS disabled is a common "gamer
    # tweak" — informational accent.
    feats = {"HRTICK": True, "START_DEBIT": True,
              "GENTLE_FAIR_SLEEPERS": False}
    v = mod.classify(feats, {}, "ok")
    assert v["verdict"] == "one_flag_non_default"


# Priority : critical_off > tuning > one_flag
def test_priority_critical_over_tuning():
    feats = {"HRTICK": False, "START_DEBIT": True,
              "GENTLE_FAIR_SLEEPERS": False}
    tunings = {"latency_ns": 24000000}
    v = mod.classify(feats, tunings, "ok")
    assert v["verdict"] == "critical_sched_flags_off"


def test_priority_tuning_over_one_flag():
    feats = {"HRTICK": True, "START_DEBIT": True,
              "GENTLE_FAIR_SLEEPERS": False}
    tunings = {"latency_ns": 24000000}
    v = mod.classify(feats, tunings, "ok")
    assert v["verdict"] == "sched_tuning_drifted"


# --- status integration ----------------------------------------

def test_status_unknown(tmp_path):
    out = mod.status(None,
                       str(tmp_path / "nope_sched"),
                       str(tmp_path / "nope_debug"))
    assert out["verdict"]["verdict"] == "unknown"


def test_status_ok_synthetic(tmp_path):
    sched = _mk_sched(
        tmp_path,
        features_text="HRTICK START_DEBIT "
                       "GENTLE_FAIR_SLEEPERS",
        latency_ns=6000000,
        min_granularity_ns=750000,
        wakeup_granularity_ns=1000000,
        migration_cost_ns=500000)
    out = mod.status(None, sched, str(tmp_path))
    assert out["read_state"] == "ok"
    assert out["verdict"]["verdict"] == "ok"


def test_status_critical_off_synthetic(tmp_path):
    sched = _mk_sched(
        tmp_path,
        features_text="NO_HRTICK START_DEBIT")
    out = mod.status(None, sched, str(tmp_path))
    assert out["ok"] is False
    assert (out["verdict"]["verdict"]
            == "critical_sched_flags_off")
