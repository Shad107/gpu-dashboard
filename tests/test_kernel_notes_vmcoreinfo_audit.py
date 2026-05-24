"""Tests for modules/kernel_notes_vmcoreinfo_audit.py — R&D #73.4."""
from __future__ import annotations

import pytest

from gpu_dashboard.modules import kernel_notes_vmcoreinfo_audit as mod


# --- classify ---------------------------------------------------

def test_classify_unknown():
    v = mod.classify(None, None, False, None, None, None,
                          False, False)
    assert v["verdict"] == "unknown"


def test_classify_requires_root():
    v = mod.classify(None, None, True, None, None, None,
                          True, False)
    assert v["verdict"] == "requires_root"


def test_classify_crash_kernel_not_reserved_zero_size():
    v = mod.classify(584, 23, True, 0, 0, 0,
                          True, True)
    assert v["verdict"] == "crash_kernel_not_reserved"


def test_classify_crash_kernel_not_loaded():
    v = mod.classify(584, 23, True, 0, 0, 1073741824,
                          True, True)
    assert v["verdict"] == "crash_kernel_not_reserved"


def test_classify_vmcoreinfo_unreadable():
    v = mod.classify(584, 0, True, 0, 1, 1073741824,
                          True, True)
    assert v["verdict"] == "vmcoreinfo_unreadable"


def test_classify_kexec_loaded_unexpectedly():
    v = mod.classify(584, 23, True, 1, 1, 1073741824,
                          True, True)
    assert v["verdict"] == "kexec_loaded_unexpectedly"


def test_classify_kernel_notes_missing():
    v = mod.classify(0, 23, True, 0, 1, 1073741824,
                          True, True)
    assert v["verdict"] == "kernel_notes_missing"


def test_classify_ok():
    v = mod.classify(584, 23, True, 0, 1, 1073741824,
                          True, True)
    assert v["verdict"] == "ok"


# Priority : crash_kernel > vmcoreinfo > kexec_loaded > notes
def test_priority_crash_over_vmcoreinfo():
    v = mod.classify(584, 0, True, 0, 0, 1073741824,
                          True, True)
    assert v["verdict"] == "crash_kernel_not_reserved"


def test_priority_vmcoreinfo_over_kexec_loaded():
    v = mod.classify(584, 0, True, 1, 1, 1073741824,
                          True, True)
    assert v["verdict"] == "vmcoreinfo_unreadable"


def test_priority_kexec_loaded_over_notes_missing():
    v = mod.classify(0, 23, True, 1, 1, 1073741824,
                          True, True)
    assert v["verdict"] == "kexec_loaded_unexpectedly"


# --- status integration -----------------------------------------

def test_status_unknown(tmp_path):
    out = mod.status(None,
                          str(tmp_path / "no_notes"),
                          str(tmp_path / "no_vmci"),
                          str(tmp_path / "no_kl"),
                          str(tmp_path / "no_kcl"),
                          str(tmp_path / "no_kcs"))
    assert out["ok"] is False
    assert out["verdict"]["verdict"] == "unknown"


def test_status_ok_synthetic(tmp_path):
    notes = tmp_path / "notes"
    notes.write_bytes(b"\x00" * 584)
    vmci = tmp_path / "vmcoreinfo"
    vmci.write_text("0x0000000100318000 1024\n")
    kl = tmp_path / "kexec_loaded"; kl.write_text("0\n")
    kcl = tmp_path / "kexec_crash_loaded"; kcl.write_text("1\n")
    kcs = tmp_path / "kexec_crash_size"
    kcs.write_text("1073741824\n")
    out = mod.status(None, str(notes), str(vmci),
                          str(kl), str(kcl), str(kcs))
    assert out["ok"] is True
    assert out["kexec_crash_loaded"] == 1
    assert out["kexec_crash_size"] == 1073741824
    assert out["verdict"]["verdict"] == "ok"


def test_status_crash_not_reserved(tmp_path):
    notes = tmp_path / "notes"
    notes.write_bytes(b"\x00" * 584)
    vmci = tmp_path / "vmcoreinfo"
    vmci.write_text("0x0000000100318000 1024\n")
    kl = tmp_path / "kexec_loaded"; kl.write_text("0\n")
    kcl = tmp_path / "kexec_crash_loaded"; kcl.write_text("0\n")
    kcs = tmp_path / "kexec_crash_size"; kcs.write_text("0\n")
    out = mod.status(None, str(notes), str(vmci),
                          str(kl), str(kcl), str(kcs))
    assert out["verdict"]["verdict"] == \
        "crash_kernel_not_reserved"
