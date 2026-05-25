"""Tests for modules/printk_pacing_audit.py R&D #106.2."""
from __future__ import annotations

import pytest

from gpu_dashboard.modules import printk_pacing_audit as mod


def test_classify_unknown():
    v = mod.classify(False, None, None, None)
    assert v["verdict"] == "unknown"


def test_classify_requires_root():
    v = mod.classify(True, None, None, None)
    assert v["verdict"] == "requires_root"


def test_classify_ok():
    v = mod.classify(True, 0, "on", 10)
    assert v["verdict"] == "ok"


def test_classify_delay_set_warn():
    v = mod.classify(True, 5, "on", 10)
    assert v["verdict"] == "printk_delay_set"


def test_classify_devkmsg_off_warn():
    v = mod.classify(True, 0, "off", 10)
    assert v["verdict"] == "printk_devkmsg_off"


def test_classify_burst_tiny_accent():
    v = mod.classify(True, 0, "on", 2)
    assert v["verdict"] == "ratelimit_burst_tiny"


def test_classify_burst_zero_not_accent():
    # 0 means kernel default (no clip from this knob)
    v = mod.classify(True, 0, "on", 0)
    assert v["verdict"] == "ok"


# Priority : delay > devkmsg > burst
def test_priority_delay_over_devkmsg():
    v = mod.classify(True, 10, "off", 2)
    assert v["verdict"] == "printk_delay_set"


def test_status_unknown(tmp_path):
    out = mod.status(None, str(tmp_path / "nope"))
    assert out["verdict"]["verdict"] == "unknown"


def test_status_ok_synthetic(tmp_path):
    d = tmp_path / "kernel"
    d.mkdir()
    (d / "printk_delay").write_text("0\n")
    (d / "printk_devkmsg").write_text("on\n")
    (d / "printk_ratelimit_burst").write_text("10\n")
    out = mod.status(None, str(d))
    assert out["verdict"]["verdict"] == "ok"
    assert out["printk_delay_ms"] == 0


def test_status_delay_set(tmp_path):
    d = tmp_path / "kernel"
    d.mkdir()
    (d / "printk_delay").write_text("100\n")
    (d / "printk_devkmsg").write_text("on\n")
    (d / "printk_ratelimit_burst").write_text("10\n")
    out = mod.status(None, str(d))
    assert out["verdict"]["verdict"] == "printk_delay_set"
