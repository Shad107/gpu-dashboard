"""Tests for modules/power_async_suspend_audit.py R&D #105.3."""
from __future__ import annotations

import pytest

from gpu_dashboard.modules import power_async_suspend_audit as mod


# --- classify --------------------------------------------------

def test_classify_unknown():
    v = mod.classify(False, None, None, None, None)
    assert v["verdict"] == "unknown"


def test_classify_requires_root():
    v = mod.classify(True, None, None, None, None)
    assert v["verdict"] == "requires_root"


def test_classify_ok():
    v = mod.classify(True, 1, 20000, 1, 0)
    assert v["verdict"] == "ok"


def test_classify_sync_off_warn():
    v = mod.classify(True, 1, 20000, 0, 0)
    assert v["verdict"] == "sync_on_suspend_off_data_risk"


def test_classify_pm_async_off_warn():
    v = mod.classify(True, 0, 20000, 1, 0)
    assert v["verdict"] == "pm_async_off_slow_resume"


def test_classify_freeze_short_accent():
    v = mod.classify(True, 1, 5000, 1, 0)
    assert v["verdict"] == "freeze_timeout_short_nvidia"


def test_classify_print_times_accent():
    v = mod.classify(True, 1, 20000, 1, 1)
    assert v["verdict"] == "pm_print_times_on_dmesg_noisy"


# Priority : sync > pm_async > freeze > print_times
def test_priority_sync_over_pm_async():
    v = mod.classify(True, 0, 20000, 0, 1)
    assert v["verdict"] == "sync_on_suspend_off_data_risk"


def test_priority_pm_async_over_freeze():
    v = mod.classify(True, 0, 5000, 1, 1)
    assert v["verdict"] == "pm_async_off_slow_resume"


def test_priority_freeze_over_print_times():
    v = mod.classify(True, 1, 5000, 1, 1)
    assert v["verdict"] == "freeze_timeout_short_nvidia"


# --- status integration ----------------------------------------

def test_status_unknown(tmp_path):
    out = mod.status(None, str(tmp_path / "nope"))
    assert out["verdict"]["verdict"] == "unknown"


def test_status_ok_synthetic(tmp_path):
    p = tmp_path / "power"
    p.mkdir()
    (p / "pm_async").write_text("1\n")
    (p / "pm_freeze_timeout").write_text("20000\n")
    (p / "sync_on_suspend").write_text("1\n")
    (p / "pm_print_times").write_text("0\n")
    out = mod.status(None, str(p))
    assert out["verdict"]["verdict"] == "ok"
    assert out["pm_freeze_timeout_ms"] == 20000


def test_status_sync_off(tmp_path):
    p = tmp_path / "power"
    p.mkdir()
    (p / "pm_async").write_text("1\n")
    (p / "pm_freeze_timeout").write_text("20000\n")
    (p / "sync_on_suspend").write_text("0\n")
    (p / "pm_print_times").write_text("0\n")
    out = mod.status(None, str(p))
    assert (out["verdict"]["verdict"]
            == "sync_on_suspend_off_data_risk")
