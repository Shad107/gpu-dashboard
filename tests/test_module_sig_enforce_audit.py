"""Tests for modules/module_sig_enforce_audit.py R&D #102.3."""
from __future__ import annotations

import pytest

from gpu_dashboard.modules import module_sig_enforce_audit as mod


# --- parse_lockdown_active -------------------------------------

def test_parse_lockdown_none():
    text = "[none] integrity confidentiality"
    assert mod.parse_lockdown_active(text) == "none"


def test_parse_lockdown_integrity():
    text = "none [integrity] confidentiality"
    assert mod.parse_lockdown_active(text) == "integrity"


def test_parse_lockdown_empty():
    assert mod.parse_lockdown_active("") is None
    assert mod.parse_lockdown_active(None) is None


# --- read_secure_boot ------------------------------------------

def test_read_sb_missing(tmp_path):
    assert mod.read_secure_boot(
        str(tmp_path / "nope")) is None


def test_read_sb_off(tmp_path):
    d = tmp_path / "efivars"
    d.mkdir()
    # 4 attribute bytes + boolean=0
    (d / "SecureBoot-abc").write_bytes(
        b"\x06\x00\x00\x00\x00")
    assert mod.read_secure_boot(str(d)) is False


def test_read_sb_on(tmp_path):
    d = tmp_path / "efivars"
    d.mkdir()
    (d / "SecureBoot-abc").write_bytes(
        b"\x06\x00\x00\x00\x01")
    assert mod.read_secure_boot(str(d)) is True


# --- classify --------------------------------------------------

def test_classify_unknown():
    v = mod.classify(None, None, None, False)
    assert v["verdict"] == "unknown"


def test_classify_requires_root():
    v = mod.classify(None, None, None, True)
    assert v["verdict"] == "requires_root"


def test_classify_ok_enforced():
    v = mod.classify("Y", "none", True, True)
    assert v["verdict"] == "ok"


def test_classify_ok_no_sb_lockdown_integrity():
    v = mod.classify("N", "integrity", False, True)
    assert v["verdict"] == "ok"


def test_classify_sb_on_no_enforce_err():
    v = mod.classify("N", "none", True, True)
    assert v["verdict"] == "sb_on_sig_enforce_off"


def test_classify_no_enforce_no_lockdown_warn():
    v = mod.classify("N", "none", False, True)
    assert v["verdict"] == "sig_enforce_off_lockdown_none"


# Priority : sb_on > no_enforce_no_lockdown
def test_priority_sb_over_lockdown_none():
    v = mod.classify("N", "none", True, True)
    assert v["verdict"] == "sb_on_sig_enforce_off"


# --- status integration ----------------------------------------

def test_status_unknown(tmp_path):
    out = mod.status(None,
                       str(tmp_path / "no_sig"),
                       str(tmp_path / "no_lockdown"),
                       str(tmp_path / "no_efi"))
    assert out["verdict"]["verdict"] == "unknown"


def test_status_ok_synthetic(tmp_path):
    sig = tmp_path / "sig_enforce"
    sig.write_text("Y\n")
    lk = tmp_path / "lockdown"
    lk.write_text("[integrity] confidentiality\n")
    efivars = tmp_path / "efivars"
    efivars.mkdir()
    (efivars / "SecureBoot-abc").write_bytes(
        b"\x06\x00\x00\x00\x01")
    out = mod.status(None, str(sig), str(lk),
                       str(efivars))
    assert out["verdict"]["verdict"] == "ok"
    assert out["sig_enforce"] == "Y"
    assert out["secure_boot"] is True


def test_status_sb_on_no_enforce(tmp_path):
    sig = tmp_path / "sig_enforce"
    sig.write_text("N\n")
    lk = tmp_path / "lockdown"
    lk.write_text("[none] integrity\n")
    efivars = tmp_path / "efivars"
    efivars.mkdir()
    (efivars / "SecureBoot-abc").write_bytes(
        b"\x06\x00\x00\x00\x01")
    out = mod.status(None, str(sig), str(lk),
                       str(efivars))
    assert (out["verdict"]["verdict"]
            == "sb_on_sig_enforce_off")
