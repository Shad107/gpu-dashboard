"""Tests for modules/ksm_advisor_audit.py R&D #101.4."""
from __future__ import annotations

import pytest

from gpu_dashboard.modules import ksm_advisor_audit as mod


# --- parse_advisor_mode ----------------------------------------

def test_parse_advisor_none():
    assert mod.parse_advisor_mode(
        "[none] scan-time") == "none"


def test_parse_advisor_scan_time():
    assert mod.parse_advisor_mode(
        "none [scan-time]") == "scan-time"


def test_parse_advisor_empty():
    assert mod.parse_advisor_mode("") is None
    assert mod.parse_advisor_mode(None) is None


# --- classify --------------------------------------------------

def test_classify_unknown():
    v = mod.classify(False, None, None, None, None)
    assert v["verdict"] == "unknown"


def test_classify_requires_root():
    v = mod.classify(True, None, None, None, None)
    assert v["verdict"] == "requires_root"


def test_classify_ksm_off_is_ok():
    v = mod.classify(True, 0, "none", 0, 30)
    # Even with bad knobs, run=0 means ok
    assert v["verdict"] == "ok"


def test_classify_running_advisor_none_warn():
    v = mod.classify(True, 1, "none", 1, 200)
    assert v["verdict"] == "ksm_running_no_advisor"


def test_classify_smart_scan_off_accent():
    v = mod.classify(True, 1, "scan-time", 0, 200)
    assert v["verdict"] == "ksm_smart_scan_off"


def test_classify_target_too_aggressive_accent():
    v = mod.classify(True, 1, "scan-time", 1, 30)
    assert v["verdict"] == "ksm_target_too_aggressive"


def test_classify_ok_running():
    v = mod.classify(True, 1, "scan-time", 1, 200)
    assert v["verdict"] == "ok"


# Priority : advisor > smart_scan > target
def test_priority_advisor_over_smart_scan():
    v = mod.classify(True, 1, "none", 0, 30)
    assert v["verdict"] == "ksm_running_no_advisor"


def test_priority_smart_scan_over_target():
    v = mod.classify(True, 1, "scan-time", 0, 30)
    assert v["verdict"] == "ksm_smart_scan_off"


# --- status integration ----------------------------------------

def test_status_unknown(tmp_path):
    out = mod.status(None, str(tmp_path / "nope"))
    assert out["verdict"]["verdict"] == "unknown"


def test_status_off_synthetic(tmp_path):
    d = tmp_path / "ksm"
    d.mkdir()
    (d / "run").write_text("0\n")
    (d / "advisor_mode").write_text("[none] scan-time\n")
    (d / "smart_scan").write_text("1\n")
    (d / "advisor_target_scan_time").write_text("200\n")
    out = mod.status(None, str(d))
    assert out["verdict"]["verdict"] == "ok"
    assert out["run"] == 0
    assert out["advisor_mode"] == "none"


def test_status_running_no_advisor(tmp_path):
    d = tmp_path / "ksm"
    d.mkdir()
    (d / "run").write_text("1\n")
    (d / "advisor_mode").write_text("[none] scan-time\n")
    (d / "smart_scan").write_text("1\n")
    (d / "advisor_target_scan_time").write_text("200\n")
    out = mod.status(None, str(d))
    assert (out["verdict"]["verdict"]
            == "ksm_running_no_advisor")
