"""Tests for modules/kernel_taint.py — R&D #36.3 kernel taint audit."""
from __future__ import annotations

from pathlib import Path

import pytest

from gpu_dashboard.modules import kernel_taint


def _mk_proc(root: Path, *, tainted: str | None = "0",
                uptime: str | None = "100.0 50.0"):
    root.mkdir(parents=True, exist_ok=True)
    sysk = root / "sys" / "kernel"
    sysk.mkdir(parents=True, exist_ok=True)
    if tainted is not None:
        (sysk / "tainted").write_text(tainted + "\n")
    if uptime is not None:
        (root / "uptime").write_text(uptime + "\n")


# --- parse_taint_bits --------------------------------------------

def test_parse_taint_bits_zero():
    assert kernel_taint.parse_taint_bits(0) == []


def test_parse_taint_bits_single():
    # bit 12 = O (out-of-tree) → 4096
    assert kernel_taint.parse_taint_bits(4096) == [12]


def test_parse_taint_bits_live_12800():
    # 12800 = bit 9 (W) + bit 12 (O) + bit 13 (E)
    assert kernel_taint.parse_taint_bits(12800) == [9, 12, 13]


def test_parse_taint_bits_bit_4_machine_check():
    # bit 4 = M (machine check)
    assert kernel_taint.parse_taint_bits(16) == [4]


# --- flag_name ---------------------------------------------------

def test_flag_name_known_bits():
    assert kernel_taint.flag_name(0)["code"] == "G/P"
    assert kernel_taint.flag_name(9)["code"] == "W"
    assert kernel_taint.flag_name(12)["code"] == "O"
    assert kernel_taint.flag_name(13)["code"] == "E"


def test_flag_name_includes_description():
    assert "warning" in kernel_taint.flag_name(9)["description"].lower()


def test_flag_name_unknown_bit():
    f = kernel_taint.flag_name(99)
    assert f["code"] == "?"


# --- classify ---------------------------------------------------

def test_classify_clean_zero():
    v = kernel_taint.classify(value=0, bits=[])
    assert v["verdict"] == "clean"


def test_classify_nvidia_normal_live_case():
    # bits 9 + 12 + 13 — exact fingerprint of NVIDIA-proprietary on Debian
    v = kernel_taint.classify(value=12800, bits=[9, 12, 13])
    assert v["verdict"] == "nvidia_normal"
    assert "nvidia" in v["reason"].lower() or "out-of-tree" in v["reason"].lower()


def test_classify_nvidia_normal_without_warning():
    # 12 + 13 only (no warning yet)
    v = kernel_taint.classify(value=12288, bits=[12, 13])
    assert v["verdict"] == "nvidia_normal"


def test_classify_warnings_only():
    # bit 9 alone — kernel warned but nothing else
    v = kernel_taint.classify(value=512, bits=[9])
    assert v["verdict"] == "warnings"
    assert "warning" in v["reason"].lower() or "dmesg" in v["recommendation"]


def test_classify_hardware_machine_check():
    # bit 4 set — hardware error
    v = kernel_taint.classify(value=16, bits=[4])
    assert v["verdict"] == "hardware_errors"
    assert "hardware" in v["reason"].lower() or "machine check" in v["reason"].lower()


def test_classify_hardware_soft_lockup():
    # bit 14 = L (soft lockup)
    v = kernel_taint.classify(value=16384, bits=[14])
    assert v["verdict"] == "hardware_errors"


def test_classify_mixed_unknown_combo():
    # bit 5 (B: bad page) + bit 9 (W) — not the nvidia signature
    v = kernel_taint.classify(value=544, bits=[5, 9])
    assert v["verdict"] == "mixed"


def test_classify_recipe_points_at_dmesg():
    # nvidia_normal has empty recipe (nothing actionable) — pick a case
    # where the recipe IS expected to point at dmesg
    v = kernel_taint.classify(value=512, bits=[9])
    assert "dmesg" in v["recommendation"].lower()


# --- read helpers ----------------------------------------------

def test_read_tainted(tmp_path):
    _mk_proc(tmp_path, tainted="12800")
    assert kernel_taint.read_tainted(str(tmp_path)) == 12800


def test_read_tainted_missing_returns_none(tmp_path):
    assert kernel_taint.read_tainted(str(tmp_path)) is None


def test_read_tainted_garbage(tmp_path):
    sysk = tmp_path / "sys" / "kernel"
    sysk.mkdir(parents=True)
    (sysk / "tainted").write_text("garbage\n")
    assert kernel_taint.read_tainted(str(tmp_path)) is None


def test_read_uptime(tmp_path):
    _mk_proc(tmp_path, uptime="195620.98 2094025.61")
    assert kernel_taint.read_uptime(str(tmp_path)) == pytest.approx(195620.98,
                                                                       rel=1e-3)


def test_read_uptime_missing_returns_none(tmp_path):
    assert kernel_taint.read_uptime(str(tmp_path)) is None


# --- status ----------------------------------------------------

def test_status_no_proc_returns_error(tmp_path, monkeypatch):
    monkeypatch.setattr(kernel_taint, "_PROC",
                          str(tmp_path / "absent"))
    s = kernel_taint.status()
    assert s["ok"] is False
    assert s["error"] == "tainted_unavailable"


def test_status_clean_kernel(tmp_path, monkeypatch):
    _mk_proc(tmp_path, tainted="0")
    monkeypatch.setattr(kernel_taint, "_PROC", str(tmp_path))
    s = kernel_taint.status()
    assert s["ok"] is True
    assert s["value"] == 0
    assert s["flags"] == []
    assert s["verdict"]["verdict"] == "clean"


def test_status_nvidia_normal_live(tmp_path, monkeypatch):
    _mk_proc(tmp_path, tainted="12800",
             uptime="195620.98 2094025.61")
    monkeypatch.setattr(kernel_taint, "_PROC", str(tmp_path))
    s = kernel_taint.status()
    assert s["value"] == 12800
    assert len(s["flags"]) == 3
    codes = [f["code"] for f in s["flags"]]
    assert "W" in codes
    assert "O" in codes
    assert "E" in codes
    assert s["verdict"]["verdict"] == "nvidia_normal"
    assert s["uptime_seconds"] == pytest.approx(195620.98, rel=1e-3)


def test_status_hardware_error_warns(tmp_path, monkeypatch):
    _mk_proc(tmp_path, tainted="16")  # bit 4
    monkeypatch.setattr(kernel_taint, "_PROC", str(tmp_path))
    s = kernel_taint.status()
    assert s["verdict"]["verdict"] == "hardware_errors"
