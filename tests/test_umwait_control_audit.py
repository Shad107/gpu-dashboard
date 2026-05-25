"""Tests for modules/umwait_control_audit.py R&D #99.1."""
from __future__ import annotations

import pytest

from gpu_dashboard.modules import umwait_control_audit as mod


# --- has_waitpkg -----------------------------------------------

def test_has_waitpkg_empty():
    assert mod.has_waitpkg(None) is False
    assert mod.has_waitpkg("") is False


def test_has_waitpkg_present():
    text = (
        "processor : 0\n"
        "flags : fpu sse sse2 avx2 waitpkg umip pku\n")
    assert mod.has_waitpkg(text) is True


def test_has_waitpkg_absent():
    text = ("flags : fpu sse sse2 avx2 umip pku\n")
    assert mod.has_waitpkg(text) is False


# --- classify --------------------------------------------------

def test_classify_unknown():
    v = mod.classify(False, False, None, None, False)
    assert v["verdict"] == "unknown"


def test_classify_requires_root():
    v = mod.classify(True, True, None, None, True)
    assert v["verdict"] == "requires_root"


def test_classify_no_waitpkg_is_ok():
    v = mod.classify(True, False, 1, 100000, False)
    assert v["verdict"] == "ok"


def test_classify_default_trap_err():
    v = mod.classify(True, True, 1, 100000, False)
    assert v["verdict"] == "umwait_c02_default_trap"


def test_classify_default_trap_high_max():
    v = mod.classify(True, True, 1, 500000, False)
    assert v["verdict"] == "umwait_c02_default_trap"


def test_classify_c02_enabled_warn():
    v = mod.classify(True, True, 1, 50000, False)
    assert v["verdict"] == "umwait_c02_enabled"


def test_classify_c02_disabled_custom_max_accent():
    v = mod.classify(True, True, 0, 50000, False)
    assert v["verdict"] == "umwait_max_time_custom"


def test_classify_ok_default():
    v = mod.classify(True, True, 0, 100000, False)
    assert v["verdict"] == "ok"


# Priority : trap > c02_enabled > custom_max
def test_priority_trap_over_c02():
    v = mod.classify(True, True, 1, 100000, False)
    assert v["verdict"] == "umwait_c02_default_trap"


def test_priority_c02_over_custom_max():
    # C0.2 on + low max_time → c02_enabled wins
    v = mod.classify(True, True, 1, 1000, False)
    assert v["verdict"] == "umwait_c02_enabled"


# --- status integration ----------------------------------------

def test_status_unknown(tmp_path):
    cpuinfo = tmp_path / "cpuinfo"
    cpuinfo.write_text("flags : fpu sse\n")
    out = mod.status(None, str(tmp_path / "nope"),
                       str(cpuinfo))
    assert out["verdict"]["verdict"] == "unknown"


def test_status_default_trap_synthetic(tmp_path):
    d = tmp_path / "umwait_control"
    d.mkdir()
    (d / "enable_c02").write_text("1\n")
    (d / "max_time").write_text("100000\n")
    cpuinfo = tmp_path / "cpuinfo"
    cpuinfo.write_text("flags : fpu sse waitpkg\n")
    out = mod.status(None, str(d), str(cpuinfo))
    assert out["verdict"]["verdict"] == "umwait_c02_default_trap"
    assert out["waitpkg"] is True


def test_status_ok_synthetic(tmp_path):
    d = tmp_path / "umwait_control"
    d.mkdir()
    (d / "enable_c02").write_text("0\n")
    (d / "max_time").write_text("100000\n")
    cpuinfo = tmp_path / "cpuinfo"
    cpuinfo.write_text("flags : fpu sse waitpkg\n")
    out = mod.status(None, str(d), str(cpuinfo))
    assert out["verdict"]["verdict"] == "ok"
