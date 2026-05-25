"""Tests for modules/io_delay_type_audit.py R&D #106.1."""
from __future__ import annotations

import pytest

from gpu_dashboard.modules import io_delay_type_audit as mod


def test_classify_unknown():
    v = mod.classify(False, None)
    assert v["verdict"] == "unknown"


def test_classify_requires_root():
    v = mod.classify(True, None)
    assert v["verdict"] == "requires_root"


def test_classify_ok_udelay():
    v = mod.classify(True, 1)
    assert v["verdict"] == "ok"


def test_classify_ok_0xed():
    v = mod.classify(True, 2)
    assert v["verdict"] == "ok"


def test_classify_legacy_accent():
    v = mod.classify(True, 0)
    assert v["verdict"] == "io_delay_legacy_slow"


def test_classify_none_warn():
    v = mod.classify(True, 3)
    assert v["verdict"] == "io_delay_none_risky"


def test_status_unknown(tmp_path):
    out = mod.status(None, str(tmp_path / "nope"))
    assert out["verdict"]["verdict"] == "unknown"


def test_status_ok_synthetic(tmp_path):
    p = tmp_path / "io_delay_type"
    p.write_text("1\n")
    out = mod.status(None, str(p))
    assert out["verdict"]["verdict"] == "ok"
    assert out["io_delay_type"] == 1


def test_status_none_risky(tmp_path):
    p = tmp_path / "io_delay_type"
    p.write_text("3\n")
    out = mod.status(None, str(p))
    assert out["verdict"]["verdict"] == "io_delay_none_risky"
