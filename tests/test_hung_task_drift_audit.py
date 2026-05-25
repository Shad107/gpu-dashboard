"""Tests for modules/hung_task_drift_audit.py R&D #104.2."""
from __future__ import annotations

import pytest

from gpu_dashboard.modules import hung_task_drift_audit as mod


# --- classify --------------------------------------------------

def test_classify_unknown():
    v = mod.classify(False, None, None, None)
    assert v["verdict"] == "unknown"


def test_classify_requires_root():
    v = mod.classify(True, None, None, None)
    assert v["verdict"] == "requires_root"


def test_classify_ok():
    v = mod.classify(True, 10, 0, 120)
    assert v["verdict"] == "ok"


def test_classify_exhausted_warn():
    v = mod.classify(True, 0, 0, 120)
    assert v["verdict"] == "hung_task_warnings_exhausted"


def test_classify_check_interval_long_accent():
    v = mod.classify(True, 10, 300, 120)
    assert v["verdict"] == "hung_task_check_interval_long"


def test_classify_decayed_accent():
    v = mod.classify(True, 3, 0, 120)
    assert v["verdict"] == "hung_task_warnings_decayed"


# Priority : exhausted > long > decayed
def test_priority_exhausted_over_long():
    v = mod.classify(True, 0, 300, 120)
    assert v["verdict"] == "hung_task_warnings_exhausted"


def test_priority_long_over_decayed():
    v = mod.classify(True, 3, 300, 120)
    assert v["verdict"] == "hung_task_check_interval_long"


# --- status integration ----------------------------------------

def test_status_unknown(tmp_path):
    out = mod.status(None, str(tmp_path / "nope"))
    assert out["verdict"]["verdict"] == "unknown"


def test_status_ok_synthetic(tmp_path):
    d = tmp_path / "kernel"
    d.mkdir()
    (d / "hung_task_warnings").write_text("10\n")
    (d / "hung_task_check_interval_secs").write_text("0\n")
    (d / "hung_task_timeout_secs").write_text("120\n")
    out = mod.status(None, str(d))
    assert out["verdict"]["verdict"] == "ok"


def test_status_exhausted_synthetic(tmp_path):
    d = tmp_path / "kernel"
    d.mkdir()
    (d / "hung_task_warnings").write_text("0\n")
    (d / "hung_task_check_interval_secs").write_text("0\n")
    (d / "hung_task_timeout_secs").write_text("120\n")
    out = mod.status(None, str(d))
    assert (out["verdict"]["verdict"]
            == "hung_task_warnings_exhausted")
