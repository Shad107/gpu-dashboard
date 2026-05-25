"""Tests for modules/nvidia_drm_params_audit.py R&D #108.1."""
from __future__ import annotations

import pytest

from gpu_dashboard.modules import nvidia_drm_params_audit as mod


def test_classify_unknown():
    v = mod.classify(False, None, None)
    assert v["verdict"] == "unknown"


def test_classify_requires_root():
    v = mod.classify(True, None, None)
    assert v["verdict"] == "requires_root"


def test_classify_ok():
    v = mod.classify(True, True, True)
    assert v["verdict"] == "ok"


def test_classify_modeset_off_err():
    v = mod.classify(True, False, True)
    assert v["verdict"] == "nvidia_drm_modeset_disabled"


def test_classify_fbdev_off_accent():
    v = mod.classify(True, True, False)
    assert v["verdict"] == "nvidia_drm_fbdev_disabled"


# Priority: modeset > fbdev
def test_priority_modeset_over_fbdev():
    v = mod.classify(True, False, False)
    assert v["verdict"] == "nvidia_drm_modeset_disabled"


def test_status_unknown(tmp_path):
    out = mod.status(None, str(tmp_path / "nope"))
    assert out["verdict"]["verdict"] == "unknown"


def test_status_ok_synthetic(tmp_path):
    d = tmp_path / "params"
    d.mkdir()
    (d / "modeset").write_text("Y\n")
    (d / "fbdev").write_text("Y\n")
    out = mod.status(None, str(d))
    assert out["verdict"]["verdict"] == "ok"


def test_status_modeset_off(tmp_path):
    d = tmp_path / "params"
    d.mkdir()
    (d / "modeset").write_text("N\n")
    (d / "fbdev").write_text("Y\n")
    out = mod.status(None, str(d))
    assert (out["verdict"]["verdict"]
            == "nvidia_drm_modeset_disabled")
