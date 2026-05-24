"""Tests for modules/dynamic_debug_audit.py — R&D #85.1."""
from __future__ import annotations

import pytest

from gpu_dashboard.modules import dynamic_debug_audit as mod


SAMPLE_LINE_OFF = (
    'kernel/printk/printk.c:1234 [printk] __my_func '
    'format "msg %d" =_\n')
SAMPLE_LINE_ON = (
    'kernel/printk/printk.c:5678 [printk] __my_func '
    'format "msg %d" =p\n')
SAMPLE_LINE_ON_PFM = (
    'drivers/usb/usb.c:42 [usb] foo format "x" =pfm\n')


# --- parse_control ---------------------------------------------

def test_parse_empty():
    assert mod.parse_control("") == (0, 0)


def test_parse_all_disabled():
    text = SAMPLE_LINE_OFF * 5
    total, enabled = mod.parse_control(text)
    assert total == 5
    assert enabled == 0


def test_parse_some_enabled():
    text = SAMPLE_LINE_OFF * 3 + SAMPLE_LINE_ON * 2
    total, enabled = mod.parse_control(text)
    assert total == 5
    assert enabled == 2


def test_parse_combined_flags():
    text = SAMPLE_LINE_ON_PFM
    total, enabled = mod.parse_control(text)
    assert total == 1
    assert enabled == 1


def test_parse_skips_blanks():
    text = "\n\n" + SAMPLE_LINE_ON + "\n"
    total, enabled = mod.parse_control(text)
    assert total == 1
    assert enabled == 1


# --- classify --------------------------------------------------

def test_classify_unknown():
    v = mod.classify("unknown", 0, 0)
    assert v["verdict"] == "unknown"


def test_classify_requires_root():
    v = mod.classify("requires_root", 0, 0)
    assert v["verdict"] == "requires_root"


def test_classify_ok():
    v = mod.classify("ok", 5000, 0)
    assert v["verdict"] == "ok"


def test_classify_some_enabled():
    v = mod.classify("ok", 5000, 25)
    assert v["verdict"] == "some_dyndbg_sites_enabled"


def test_classify_many_enabled():
    v = mod.classify("ok", 5000, 600)
    assert v["verdict"] == "many_dyndbg_sites_enabled"


def test_classify_at_floor_some():
    # Exactly at floor (500) is still "some"
    v = mod.classify("ok", 5000, 500)
    assert v["verdict"] == "some_dyndbg_sites_enabled"


# --- status integration ----------------------------------------

def test_status_unknown(tmp_path):
    out = mod.status(None,
                       str(tmp_path / "nope_control"),
                       str(tmp_path / "nope_debugfs"))
    assert out["verdict"]["verdict"] == "unknown"


def test_status_ok_synthetic(tmp_path):
    debug = tmp_path / "debug"
    ddir = debug / "dynamic_debug"
    ddir.mkdir(parents=True)
    (ddir / "control").write_text(SAMPLE_LINE_OFF * 100)
    out = mod.status(None, str(ddir / "control"), str(debug))
    assert out["read_state"] == "ok"
    assert out["total_sites"] == 100
    assert out["enabled_sites"] == 0
    assert out["verdict"]["verdict"] == "ok"


def test_status_some_enabled_synthetic(tmp_path):
    debug = tmp_path / "debug"
    ddir = debug / "dynamic_debug"
    ddir.mkdir(parents=True)
    (ddir / "control").write_text(
        SAMPLE_LINE_OFF * 95 + SAMPLE_LINE_ON * 5)
    out = mod.status(None, str(ddir / "control"), str(debug))
    assert out["enabled_sites"] == 5
    assert (out["verdict"]["verdict"]
            == "some_dyndbg_sites_enabled")


def test_status_many_enabled_synthetic(tmp_path):
    debug = tmp_path / "debug"
    ddir = debug / "dynamic_debug"
    ddir.mkdir(parents=True)
    (ddir / "control").write_text(
        SAMPLE_LINE_ON * 600)
    out = mod.status(None, str(ddir / "control"), str(debug))
    assert out["enabled_sites"] == 600
    assert (out["verdict"]["verdict"]
            == "many_dyndbg_sites_enabled")
