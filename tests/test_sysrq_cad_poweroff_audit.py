"""Tests for modules/sysrq_cad_poweroff_audit.py R&D #107.2."""
from __future__ import annotations

import pytest

from gpu_dashboard.modules import sysrq_cad_poweroff_audit as mod


def test_classify_unknown():
    v = mod.classify(False, None, None)
    assert v["verdict"] == "unknown"


def test_classify_requires_root():
    v = mod.classify(True, None, None)
    assert v["verdict"] == "requires_root"


def test_classify_ok():
    v = mod.classify(True, 0, "/sbin/poweroff")
    assert v["verdict"] == "ok"


def test_classify_cad_hard_reboot_warn():
    v = mod.classify(True, 1, "/sbin/poweroff")
    assert v["verdict"] == "cad_hard_reboot"


def test_classify_poweroff_overridden_accent():
    v = mod.classify(True, 0, "/usr/local/bin/my-shutdown")
    assert v["verdict"] == "poweroff_cmd_overridden"


def test_classify_empty_poweroff_is_ok():
    v = mod.classify(True, 0, "")
    assert v["verdict"] == "ok"


# Priority : cad > poweroff
def test_priority_cad_over_poweroff():
    v = mod.classify(True, 1, "/custom")
    assert v["verdict"] == "cad_hard_reboot"


def test_status_unknown(tmp_path):
    out = mod.status(None, str(tmp_path / "nope"))
    assert out["verdict"]["verdict"] == "unknown"


def test_status_ok(tmp_path):
    d = tmp_path / "kernel"
    d.mkdir()
    (d / "ctrl-alt-del").write_text("0\n")
    (d / "poweroff_cmd").write_text("/sbin/poweroff\n")
    out = mod.status(None, str(d))
    assert out["verdict"]["verdict"] == "ok"


def test_status_cad_hard(tmp_path):
    d = tmp_path / "kernel"
    d.mkdir()
    (d / "ctrl-alt-del").write_text("1\n")
    (d / "poweroff_cmd").write_text("/sbin/poweroff\n")
    out = mod.status(None, str(d))
    assert out["verdict"]["verdict"] == "cad_hard_reboot"
