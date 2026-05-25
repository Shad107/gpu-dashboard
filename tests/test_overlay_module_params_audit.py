"""Tests for modules/overlay_module_params_audit.py R&D #108.2."""
from __future__ import annotations

import pytest

from gpu_dashboard.modules import overlay_module_params_audit as mod


def test_classify_unknown():
    v = mod.classify(False, None, None, None)
    assert v["verdict"] == "unknown"


def test_classify_requires_root():
    v = mod.classify(True, None, None, None)
    assert v["verdict"] == "requires_root"


def test_classify_ok():
    v = mod.classify(True, True, True, True)
    assert v["verdict"] == "ok"


def test_classify_redirect_dir_off_warn():
    v = mod.classify(True, True, False, True)
    assert v["verdict"] == "overlay_redirect_dir_off"


def test_classify_metacopy_off_accent():
    v = mod.classify(True, False, True, True)
    assert v["verdict"] == "overlay_metacopy_off"


def test_classify_xino_auto_off_accent():
    v = mod.classify(True, True, True, False)
    assert v["verdict"] == "overlay_xino_auto_off"


# Priority : redirect_dir > metacopy > xino_auto
def test_priority_redirect_over_metacopy():
    v = mod.classify(True, False, False, True)
    assert v["verdict"] == "overlay_redirect_dir_off"


def test_priority_metacopy_over_xino():
    v = mod.classify(True, False, True, False)
    assert v["verdict"] == "overlay_metacopy_off"


def test_status_unknown(tmp_path):
    out = mod.status(None, str(tmp_path / "nope"))
    assert out["verdict"]["verdict"] == "unknown"


def test_status_ok(tmp_path):
    d = tmp_path / "params"
    d.mkdir()
    (d / "metacopy").write_text("Y\n")
    (d / "redirect_dir").write_text("Y\n")
    (d / "xino_auto").write_text("Y\n")
    out = mod.status(None, str(d))
    assert out["verdict"]["verdict"] == "ok"


def test_status_redirect_off(tmp_path):
    d = tmp_path / "params"
    d.mkdir()
    (d / "metacopy").write_text("N\n")
    (d / "redirect_dir").write_text("N\n")
    (d / "xino_auto").write_text("Y\n")
    out = mod.status(None, str(d))
    assert (out["verdict"]["verdict"]
            == "overlay_redirect_dir_off")
