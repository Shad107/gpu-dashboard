"""Tests for modules/userspace_hardening_sysctls_audit.py
R&D #88.1."""
from __future__ import annotations

import pytest

from gpu_dashboard.modules import (
    userspace_hardening_sysctls_audit as mod)


def _mk_sysctls(tmp_path, *, va=2, hard=1, sym=1,
                 fifos=2, regular=2, suid=0):
    root = tmp_path / "sys"
    (root / "kernel").mkdir(parents=True, exist_ok=True)
    (root / "fs").mkdir(parents=True, exist_ok=True)
    (root / "kernel" / "randomize_va_space").write_text(
        f"{va}\n")
    (root / "fs" / "protected_hardlinks").write_text(
        f"{hard}\n")
    (root / "fs" / "protected_symlinks").write_text(
        f"{sym}\n")
    (root / "fs" / "protected_fifos").write_text(
        f"{fifos}\n")
    (root / "fs" / "protected_regular").write_text(
        f"{regular}\n")
    (root / "fs" / "suid_dumpable").write_text(
        f"{suid}\n")
    return str(root)


# --- read_sysctls ----------------------------------------------

def test_read_sysctls_missing(tmp_path):
    assert mod.read_sysctls(str(tmp_path / "nope")) == {}


def test_read_sysctls_populated(tmp_path):
    r = _mk_sysctls(tmp_path)
    out = mod.read_sysctls(r)
    assert out["randomize_va_space"] == 2
    assert out["protected_symlinks"] == 1
    assert out["suid_dumpable"] == 0
    assert len(out) == 6


def test_read_sysctls_garbage_value(tmp_path):
    root = tmp_path / "sys"
    (root / "kernel").mkdir(parents=True)
    (root / "kernel" / "randomize_va_space").write_text(
        "garbage\n")
    out = mod.read_sysctls(str(root))
    assert "randomize_va_space" not in out


# --- classify --------------------------------------------------

def test_classify_unknown_empty():
    v = mod.classify({})
    assert v["verdict"] == "unknown"


def test_classify_hardened():
    v = mod.classify({
        "randomize_va_space": 2,
        "protected_hardlinks": 1,
        "protected_symlinks": 1,
        "protected_fifos": 2,
        "protected_regular": 2,
        "suid_dumpable": 0,
    })
    assert v["verdict"] == "hardened"


def test_classify_aslr_disabled():
    v = mod.classify({
        "randomize_va_space": 0,
        "protected_hardlinks": 1,
        "protected_symlinks": 1,
        "protected_fifos": 2,
        "protected_regular": 2,
        "suid_dumpable": 0,
    })
    assert v["verdict"] == "aslr_disabled"


def test_classify_suid_dumpable_world():
    v = mod.classify({
        "randomize_va_space": 2,
        "protected_hardlinks": 1,
        "protected_symlinks": 1,
        "protected_fifos": 2,
        "protected_regular": 2,
        "suid_dumpable": 1,
    })
    assert v["verdict"] == "suid_dumpable_world"


def test_classify_protected_symlinks_off():
    v = mod.classify({
        "randomize_va_space": 2,
        "protected_hardlinks": 1,
        "protected_symlinks": 0,
        "protected_fifos": 2,
        "protected_regular": 2,
        "suid_dumpable": 0,
    })
    assert v["verdict"] == "protected_symlinks_off"


def test_classify_protected_hardlinks_off():
    v = mod.classify({
        "randomize_va_space": 2,
        "protected_hardlinks": 0,
        "protected_symlinks": 1,
        "protected_fifos": 2,
        "protected_regular": 2,
        "suid_dumpable": 0,
    })
    assert v["verdict"] == "protected_hardlinks_off"


def test_classify_fifos_or_regular_under_2():
    v1 = mod.classify({
        "randomize_va_space": 2,
        "protected_hardlinks": 1,
        "protected_symlinks": 1,
        "protected_fifos": 1,
        "protected_regular": 2,
        "suid_dumpable": 0,
    })
    assert v1["verdict"] == "protected_fifos_regular_off"
    v2 = mod.classify({
        "randomize_va_space": 2,
        "protected_hardlinks": 1,
        "protected_symlinks": 1,
        "protected_fifos": 2,
        "protected_regular": 1,
        "suid_dumpable": 0,
    })
    assert v2["verdict"] == "protected_fifos_regular_off"


# Priority : aslr > suid_dumpable > sym > hard > fifos
def test_priority_aslr_over_suid():
    v = mod.classify({
        "randomize_va_space": 0,
        "protected_hardlinks": 1,
        "protected_symlinks": 1,
        "protected_fifos": 2,
        "protected_regular": 2,
        "suid_dumpable": 1,
    })
    assert v["verdict"] == "aslr_disabled"


def test_priority_suid_over_symlinks():
    v = mod.classify({
        "randomize_va_space": 2,
        "protected_hardlinks": 0,
        "protected_symlinks": 0,
        "protected_fifos": 1,
        "protected_regular": 1,
        "suid_dumpable": 1,
    })
    assert v["verdict"] == "suid_dumpable_world"


def test_priority_symlinks_over_hardlinks():
    v = mod.classify({
        "randomize_va_space": 2,
        "protected_hardlinks": 0,
        "protected_symlinks": 0,
        "protected_fifos": 2,
        "protected_regular": 2,
        "suid_dumpable": 0,
    })
    assert v["verdict"] == "protected_symlinks_off"


# --- status integration ----------------------------------------

def test_status_unknown(tmp_path):
    out = mod.status(None, str(tmp_path / "nope"))
    assert out["verdict"]["verdict"] == "unknown"
    assert out["ok"] is False


def test_status_hardened(tmp_path):
    r = _mk_sysctls(tmp_path)
    out = mod.status(None, r)
    assert out["verdict"]["verdict"] == "hardened"
    assert out["ok"] is True
    assert out["sysctls"]["randomize_va_space"] == 2


def test_status_aslr_disabled(tmp_path):
    r = _mk_sysctls(tmp_path, va=0)
    out = mod.status(None, r)
    assert out["verdict"]["verdict"] == "aslr_disabled"
    assert out["ok"] is False


def test_status_accent_fifos(tmp_path):
    r = _mk_sysctls(tmp_path, fifos=1)
    out = mod.status(None, r)
    assert out["verdict"]["verdict"] == "protected_fifos_regular_off"
    assert out["ok"] is False
