"""Tests for modules/kvm_mmu_audit.py — R&D #97.1."""
from __future__ import annotations

import pytest

from gpu_dashboard.modules import kvm_mmu_audit as mod


def _mk_kvm(tmp_path, *, tdp_mmu="Y", nx_huge_pages="Y",
              recovery_ratio="60"):
    d = tmp_path / "kvm"
    d.mkdir(parents=True, exist_ok=True)
    if tdp_mmu is not None:
        (d / "tdp_mmu").write_text(tdp_mmu + "\n")
    if nx_huge_pages is not None:
        (d / "nx_huge_pages").write_text(
            nx_huge_pages + "\n")
    if recovery_ratio is not None:
        (d / "nx_huge_pages_recovery_ratio").write_text(
            recovery_ratio + "\n")
    return str(d)


def _mk_intel(tmp_path, *, ept="Y"):
    d = tmp_path / "kvm_intel"
    d.mkdir(parents=True, exist_ok=True)
    if ept is not None:
        (d / "ept").write_text(ept + "\n")
    return str(d)


def _mk_amd(tmp_path, *, npt="Y"):
    d = tmp_path / "kvm_amd"
    d.mkdir(parents=True, exist_ok=True)
    if npt is not None:
        (d / "npt").write_text(npt + "\n")
    return str(d)


# --- _is_yes ---------------------------------------------------

def test_is_yes_y():
    assert mod._is_yes("Y") is True
    assert mod._is_yes("1") is True
    assert mod._is_yes("true") is True


def test_is_yes_n():
    assert mod._is_yes("N") is False
    assert mod._is_yes("0") is False


def test_is_yes_garbage():
    assert mod._is_yes("zzz") is None
    assert mod._is_yes(None) is None


# --- read_state ------------------------------------------------

def test_read_state_no_kvm(tmp_path):
    s = mod.read_state(
        str(tmp_path / "nope_kvm"),
        str(tmp_path / "nope_intel"),
        str(tmp_path / "nope_amd"))
    assert s["kvm_present"] is False


def test_read_state_full(tmp_path):
    k = _mk_kvm(tmp_path)
    i = _mk_intel(tmp_path)
    s = mod.read_state(k, i, str(tmp_path / "no_amd"))
    assert s["kvm_present"] is True
    assert s["nx_huge_pages"] is True
    assert s["intel_ept"] is True
    assert s["amd_present"] is False


# --- classify --------------------------------------------------

def _state(**overrides):
    base = {
        "kvm_present": True,
        "tdp_mmu": True,
        "nx_huge_pages": True,
        "nx_recovery_ratio": 60,
        "intel_ept": True,
        "amd_npt": None,
        "intel_present": True,
        "amd_present": False,
    }
    base.update(overrides)
    return base


def test_classify_unknown_no_kvm():
    v = mod.classify(_state(kvm_present=False))
    assert v["verdict"] == "unknown"


def test_classify_requires_root():
    v = mod.classify(_state(
        tdp_mmu=None, nx_huge_pages=None))
    assert v["verdict"] == "requires_root"


def test_classify_ok():
    v = mod.classify(_state())
    assert v["verdict"] == "ok"


def test_classify_nx_disabled_err():
    v = mod.classify(_state(nx_huge_pages=False))
    assert v["verdict"] == "nx_huge_pages_disabled"


def test_classify_intel_ept_off_warn():
    v = mod.classify(_state(intel_ept=False))
    assert v["verdict"] == "ept_npt_off"


def test_classify_amd_npt_off_warn():
    v = mod.classify(_state(
        intel_present=False, intel_ept=None,
        amd_present=True, amd_npt=False))
    assert v["verdict"] == "ept_npt_off"


def test_classify_tdp_mmu_off_warn():
    v = mod.classify(_state(tdp_mmu=False))
    assert v["verdict"] == "tdp_mmu_off"


def test_classify_recovery_ratio_zero_accent():
    v = mod.classify(_state(nx_recovery_ratio=0))
    assert v["verdict"] == "recovery_ratio_zero"


# Priority : nx_disabled > ept/npt_off > tdp_mmu_off > recovery
def test_priority_nx_over_ept():
    v = mod.classify(_state(
        nx_huge_pages=False, intel_ept=False))
    assert v["verdict"] == "nx_huge_pages_disabled"


def test_priority_ept_over_tdp_mmu():
    v = mod.classify(_state(
        intel_ept=False, tdp_mmu=False))
    assert v["verdict"] == "ept_npt_off"


# --- status integration ----------------------------------------

def test_status_unknown(tmp_path):
    out = mod.status(None,
                       str(tmp_path / "nope_kvm"),
                       str(tmp_path / "nope_intel"),
                       str(tmp_path / "nope_amd"))
    assert out["verdict"]["verdict"] == "unknown"


def test_status_ok_synthetic(tmp_path):
    k = _mk_kvm(tmp_path)
    i = _mk_intel(tmp_path)
    out = mod.status(None, k, i,
                       str(tmp_path / "no_amd"))
    assert out["verdict"]["verdict"] == "ok"


def test_status_nx_disabled_synthetic(tmp_path):
    k = _mk_kvm(tmp_path, nx_huge_pages="N")
    i = _mk_intel(tmp_path)
    out = mod.status(None, k, i,
                       str(tmp_path / "no_amd"))
    assert (out["verdict"]["verdict"]
            == "nx_huge_pages_disabled")
    assert out["ok"] is False
