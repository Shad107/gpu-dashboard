"""Tests for modules/sysrq_mask_audit.py — R&D #82.1."""
from __future__ import annotations

import pytest

from gpu_dashboard.modules import sysrq_mask_audit as mod


def _mk_kernel(tmp_path, sysrq=None, kexec=None,
                always_enabled=None):
    d = tmp_path / "kernel"
    d.mkdir(parents=True, exist_ok=True)
    if sysrq is not None:
        (d / "sysrq").write_text(f"{sysrq}\n")
    if kexec is not None:
        (d / "kexec_load_disabled").write_text(f"{kexec}\n")
    if always_enabled is not None:
        (d / "sysrq_always_enabled").write_text(
            f"{always_enabled}\n")
    return str(d)


# --- read_kernel -----------------------------------------------

def test_read_missing(tmp_path):
    out = mod.read_kernel(str(tmp_path / "nope"))
    assert out["sysrq"] is None
    assert out["kexec_load_disabled"] is None


def test_read_populated(tmp_path):
    r = _mk_kernel(tmp_path, sysrq=176, kexec=0)
    out = mod.read_kernel(r)
    assert out["sysrq"] == 176
    assert out["kexec_load_disabled"] == 0
    assert out["sysrq_always_enabled"] is None


# --- _bits_set -------------------------------------------------

def test_bits_set_none():
    assert mod._bits_set(0) == []


def test_bits_set_sak():
    out = mod._bits_set(mod.BIT_SAK)
    assert len(out) == 1
    assert "SAK" in out[0]


def test_bits_set_dump():
    out = mod._bits_set(mod.BIT_DUMP)
    assert len(out) == 1
    assert "dump" in out[0]


def test_bits_set_both():
    out = mod._bits_set(mod.BIT_SAK | mod.BIT_DUMP)
    assert len(out) == 2


def test_bits_set_safe_bits_ignored():
    # 0x10 (remount-ro) + 0x80 (nice RT) — not risky
    assert mod._bits_set(0x10 | 0x80) == []


# --- classify --------------------------------------------------

def test_classify_unknown():
    v = mod.classify({"sysrq": None,
                          "kexec_load_disabled": None,
                          "sysrq_always_enabled": None})
    assert v["verdict"] == "unknown"


def test_classify_ok_disabled():
    v = mod.classify({"sysrq": 0,
                          "kexec_load_disabled": 1,
                          "sysrq_always_enabled": None})
    assert v["verdict"] == "ok"


def test_classify_ok_safe_subset():
    # value 176 = 0xB0 = 0x10 + 0x20 + 0x80
    v = mod.classify({"sysrq": 176,
                          "kexec_load_disabled": 1,
                          "sysrq_always_enabled": None})
    assert v["verdict"] == "ok"


def test_classify_full_with_kexec():
    v = mod.classify({"sysrq": 1,
                          "kexec_load_disabled": 0,
                          "sysrq_always_enabled": None})
    assert v["verdict"] == "sysrq_full_with_kexec"


def test_classify_full_with_kexec_255():
    v = mod.classify({"sysrq": 255,
                          "kexec_load_disabled": 0,
                          "sysrq_always_enabled": None})
    assert v["verdict"] == "sysrq_full_with_kexec"


def test_classify_full_kexec_missing():
    # kexec field missing → assume worst (treat as enabled)
    v = mod.classify({"sysrq": 1,
                          "kexec_load_disabled": None,
                          "sysrq_always_enabled": None})
    assert v["verdict"] == "sysrq_full_with_kexec"


def test_classify_full_kexec_locked():
    v = mod.classify({"sysrq": 1,
                          "kexec_load_disabled": 1,
                          "sysrq_always_enabled": None})
    assert v["verdict"] == "sysrq_full_enabled"


def test_classify_risky_subset_sak():
    v = mod.classify({"sysrq": 0x02,
                          "kexec_load_disabled": 1,
                          "sysrq_always_enabled": None})
    assert v["verdict"] == "sysrq_risky_subset"
    assert any("SAK" in b for b in v["risky_bits"])


def test_classify_risky_subset_dump():
    v = mod.classify({"sysrq": 0x06,  # SAK + dump
                          "kexec_load_disabled": 1,
                          "sysrq_always_enabled": None})
    assert v["verdict"] == "sysrq_risky_subset"


# Priority : full_with_kexec > full > risky_subset > ok
def test_priority_full_kexec_over_full():
    v = mod.classify({"sysrq": 1,
                          "kexec_load_disabled": 0,
                          "sysrq_always_enabled": None})
    assert v["verdict"] == "sysrq_full_with_kexec"


def test_priority_full_over_risky():
    # value 1 means full → wins over risky-subset check
    v = mod.classify({"sysrq": 1,
                          "kexec_load_disabled": 1,
                          "sysrq_always_enabled": None})
    assert v["verdict"] == "sysrq_full_enabled"


# --- status integration ----------------------------------------

def test_status_unknown(tmp_path):
    out = mod.status(None, str(tmp_path / "nope"))
    assert out["ok"] is False
    assert out["verdict"]["verdict"] == "unknown"


def test_status_ok(tmp_path):
    r = _mk_kernel(tmp_path, sysrq=176, kexec=1)
    out = mod.status(None, r)
    assert out["ok"] is True
    assert out["values"]["sysrq"] == 176
    assert out["verdict"]["verdict"] == "ok"


def test_status_full_with_kexec(tmp_path):
    r = _mk_kernel(tmp_path, sysrq=1, kexec=0)
    out = mod.status(None, r)
    assert out["ok"] is False
    assert (out["verdict"]["verdict"]
            == "sysrq_full_with_kexec")
