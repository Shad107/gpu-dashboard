"""Tests for modules/firmware_loader_policy_audit.py R&D #104.3."""
from __future__ import annotations

import pytest

from gpu_dashboard.modules import firmware_loader_policy_audit as mod


# --- classify --------------------------------------------------

def test_classify_unknown():
    v = mod.classify(False, None, None, None)
    assert v["verdict"] == "unknown"


def test_classify_requires_root():
    v = mod.classify(True, None, None, None)
    assert v["verdict"] == "requires_root"


def test_classify_ok():
    v = mod.classify(True, 60, 0, 0)
    assert v["verdict"] == "ok"


def test_classify_fallback_disabled_warn():
    v = mod.classify(True, 60, 0, 1)
    assert v["verdict"] == "fw_fallback_disabled"


def test_classify_timeout_short_warn():
    v = mod.classify(True, 5, 0, 0)
    assert v["verdict"] == "fw_timeout_too_short"


def test_classify_fallback_forced_accent():
    v = mod.classify(True, 60, 1, 0)
    assert v["verdict"] == "fw_fallback_forced"


# Priority : fallback_disabled > timeout_short > fallback_forced
def test_priority_disabled_over_timeout():
    v = mod.classify(True, 5, 0, 1)
    assert v["verdict"] == "fw_fallback_disabled"


def test_priority_timeout_over_forced():
    v = mod.classify(True, 5, 1, 0)
    assert v["verdict"] == "fw_timeout_too_short"


# --- status integration ----------------------------------------

def test_status_unknown(tmp_path):
    out = mod.status(None,
                       str(tmp_path / "no_fw"),
                       str(tmp_path / "no_config"))
    assert out["verdict"]["verdict"] == "unknown"


def test_status_ok_synthetic(tmp_path):
    c = tmp_path / "firmware"
    c.mkdir()
    (c / "timeout").write_text("60\n")
    out = mod.status(None, str(c),
                       str(tmp_path / "no_config"))
    assert out["verdict"]["verdict"] == "ok"
    assert out["timeout_s"] == 60


def test_status_timeout_short(tmp_path):
    c = tmp_path / "firmware"
    c.mkdir()
    (c / "timeout").write_text("5\n")
    out = mod.status(None, str(c),
                       str(tmp_path / "no_config"))
    assert (out["verdict"]["verdict"]
            == "fw_timeout_too_short")
