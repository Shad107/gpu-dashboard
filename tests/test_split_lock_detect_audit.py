"""Tests for modules/split_lock_detect_audit.py R&D #99.2."""
from __future__ import annotations

import pytest

from gpu_dashboard.modules import split_lock_detect_audit as mod


# --- is_intel --------------------------------------------------

def test_is_intel_yes():
    text = "vendor_id\t: GenuineIntel\n"
    assert mod.is_intel(text) is True


def test_is_intel_amd():
    text = "vendor_id\t: AuthenticAMD\n"
    assert mod.is_intel(text) is False


def test_is_intel_empty():
    assert mod.is_intel(None) is False
    assert mod.is_intel("") is False


# --- parse_cmdline_mode ----------------------------------------

def test_parse_cmdline_none():
    assert mod.parse_cmdline_mode("") is None
    assert mod.parse_cmdline_mode(None) is None


def test_parse_cmdline_fatal():
    text = "BOOT_IMAGE=/vmlinuz split_lock_detect=fatal ro\n"
    assert mod.parse_cmdline_mode(text) == "fatal"


def test_parse_cmdline_ratelimit():
    text = "split_lock_detect=ratelimit:10 ro\n"
    assert mod.parse_cmdline_mode(text) == "ratelimit:10"


def test_parse_cmdline_no_split_lock():
    text = "BOOT_IMAGE=/vmlinuz ro quiet\n"
    assert mod.parse_cmdline_mode(text) is None


# --- classify --------------------------------------------------

def test_classify_unknown_non_intel():
    v = mod.classify(False, None, None, True)
    assert v["verdict"] == "unknown"


def test_classify_unknown_no_sysctl():
    v = mod.classify(True, None, None, False)
    assert v["verdict"] == "unknown"


def test_classify_requires_root():
    v = mod.classify(True, None, None, True)
    assert v["verdict"] == "requires_root"


def test_classify_ok_default():
    v = mod.classify(True, None, 1, True)
    assert v["verdict"] == "ok"


def test_classify_fatal_err():
    v = mod.classify(True, "fatal", 1, True)
    assert v["verdict"] == "split_lock_fatal"


def test_classify_off_warn_cmdline():
    v = mod.classify(True, "off", 1, True)
    assert v["verdict"] == "split_lock_off"


def test_classify_off_warn_sysctl():
    v = mod.classify(True, None, 0, True)
    assert v["verdict"] == "split_lock_off"


def test_classify_ratelimit_accent():
    v = mod.classify(True, "ratelimit:10", 1, True)
    assert v["verdict"] == "split_lock_ratelimited"


# Priority : fatal > off > ratelimit > ok
def test_priority_fatal_over_ratelimit():
    # cmdline=fatal should win even if sysctl is whatever
    v = mod.classify(True, "fatal", 0, True)
    assert v["verdict"] == "split_lock_fatal"


def test_priority_off_over_ratelimit():
    v = mod.classify(True, "off", 1, True)
    assert v["verdict"] == "split_lock_off"


# --- status integration ----------------------------------------

def test_status_unknown_non_intel(tmp_path):
    cpuinfo = tmp_path / "cpuinfo"
    cpuinfo.write_text("vendor_id\t: AuthenticAMD\n")
    cmdline = tmp_path / "cmdline"
    cmdline.write_text("ro\n")
    sysctl = tmp_path / "mitigate"
    out = mod.status(None, str(cpuinfo), str(cmdline),
                       str(sysctl))
    assert out["verdict"]["verdict"] == "unknown"
    assert out["intel"] is False


def test_status_ok_default(tmp_path):
    cpuinfo = tmp_path / "cpuinfo"
    cpuinfo.write_text("vendor_id\t: GenuineIntel\n")
    cmdline = tmp_path / "cmdline"
    cmdline.write_text("ro quiet\n")
    sysctl = tmp_path / "mitigate"
    sysctl.write_text("1\n")
    out = mod.status(None, str(cpuinfo), str(cmdline),
                       str(sysctl))
    assert out["verdict"]["verdict"] == "ok"


def test_status_fatal(tmp_path):
    cpuinfo = tmp_path / "cpuinfo"
    cpuinfo.write_text("vendor_id\t: GenuineIntel\n")
    cmdline = tmp_path / "cmdline"
    cmdline.write_text("ro split_lock_detect=fatal\n")
    sysctl = tmp_path / "mitigate"
    sysctl.write_text("1\n")
    out = mod.status(None, str(cpuinfo), str(cmdline),
                       str(sysctl))
    assert out["verdict"]["verdict"] == "split_lock_fatal"
    assert out["ok"] is False
