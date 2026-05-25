"""Tests for modules/hwp_dynamic_boost_audit.py R&D #104.1."""
from __future__ import annotations

import pytest

from gpu_dashboard.modules import hwp_dynamic_boost_audit as mod


# --- classify --------------------------------------------------

def test_classify_unknown_no_driver():
    v = mod.classify(False, None, None, None)
    assert v["verdict"] == "unknown"


def test_classify_unknown_status_passive():
    v = mod.classify(True, "passive", 1, "balance_power")
    assert v["verdict"] == "unknown"


def test_classify_requires_root():
    v = mod.classify(True, "active", None, None)
    assert v["verdict"] == "requires_root"


def test_classify_ok():
    v = mod.classify(True, "active", 1, "balance_performance")
    assert v["verdict"] == "ok"


def test_classify_boost_off_warn():
    v = mod.classify(True, "active", 0, "balance_performance")
    assert v["verdict"] == "hwp_boost_off_on_desktop"


def test_classify_fights_epp_accent():
    v = mod.classify(True, "active", 1, "power")
    assert v["verdict"] == "hwp_boost_fights_epp"


def test_classify_status_none_ok_path():
    # status unreadable but boost+epp readable
    v = mod.classify(True, None, 1, "balance_performance")
    assert v["verdict"] == "ok"


# Priority : boost_off > fights_epp
def test_priority_boost_off_over_fights():
    # boost=0 + EPP=power → boost_off (since boost off takes
    # priority over boost-fights-EPP which requires boost=1)
    v = mod.classify(True, "active", 0, "power")
    assert v["verdict"] == "hwp_boost_off_on_desktop"


# --- status integration ----------------------------------------

def test_status_unknown(tmp_path):
    out = mod.status(None,
                       str(tmp_path / "no_pstate"),
                       str(tmp_path / "no_epp"))
    assert out["verdict"]["verdict"] == "unknown"


def test_status_ok_synthetic(tmp_path):
    p = tmp_path / "pstate"
    p.mkdir()
    (p / "status").write_text("active\n")
    (p / "hwp_dynamic_boost").write_text("1\n")
    e = tmp_path / "epp"
    e.write_text("balance_performance\n")
    out = mod.status(None, str(p), str(e))
    assert out["verdict"]["verdict"] == "ok"
    assert out["hwp_dynamic_boost"] == 1


def test_status_boost_off(tmp_path):
    p = tmp_path / "pstate"
    p.mkdir()
    (p / "status").write_text("active\n")
    (p / "hwp_dynamic_boost").write_text("0\n")
    e = tmp_path / "epp"
    e.write_text("balance_performance\n")
    out = mod.status(None, str(p), str(e))
    assert (out["verdict"]["verdict"]
            == "hwp_boost_off_on_desktop")
