"""Tests for modules/lru_gen_mglru_audit.py — R&D #68.2."""
from __future__ import annotations

import pytest

from gpu_dashboard.modules import lru_gen_mglru_audit as mod


# --- read_enabled / read_min_ttl --------------------------------

def test_read_enabled(tmp_path):
    p = tmp_path / "enabled"
    p.write_text("0x0007\n")
    assert mod.read_enabled(str(p)) == 7


def test_read_enabled_missing(tmp_path):
    assert mod.read_enabled(str(tmp_path / "nope")) is None


def test_read_min_ttl_zero(tmp_path):
    p = tmp_path / "min_ttl_ms"
    p.write_text("0\n")
    assert mod.read_min_ttl(str(p)) == 0


def test_read_min_ttl(tmp_path):
    p = tmp_path / "min_ttl_ms"
    p.write_text("500\n")
    assert mod.read_min_ttl(str(p)) == 500


# --- read_swap_used_kib -----------------------------------------

def test_read_swap_used_missing(tmp_path):
    assert mod.read_swap_used_kib(str(tmp_path / "nope")) == 0


def test_read_swap_used_zero(tmp_path):
    p = tmp_path / "swaps"
    p.write_text("Filename Type Size Used Priority\n")
    assert mod.read_swap_used_kib(str(p)) == 0


def test_read_swap_used(tmp_path):
    p = tmp_path / "swaps"
    p.write_text("Filename Type Size Used Priority\n"
                    "/swap.img file 8388604 200000 -2\n"
                    "/dev/sda2 partition 1048576 50000 -3\n")
    assert mod.read_swap_used_kib(str(p)) == 250000


# --- read_psi_memory_full_avg60 ---------------------------------

def test_read_psi_missing(tmp_path):
    assert mod.read_psi_memory_full_avg60(
        str(tmp_path / "nope")) is None


def test_read_psi(tmp_path):
    p = tmp_path / "memory"
    p.write_text("some avg10=0.00 avg60=0.00 avg300=0.00 total=10\n"
                    "full avg10=0.10 avg60=2.50 avg300=0.40 total=20\n")
    assert mod.read_psi_memory_full_avg60(str(p)) == 2.5


# --- classify ---------------------------------------------------

def test_classify_unknown_absent():
    v = mod.classify(None, None, 0, None, None, False)
    assert v["verdict"] == "unknown"


def test_classify_mglru_disabled_swap_pressure():
    v = mod.classify(0, 0, 200_000, 0.0, True, True)
    assert v["verdict"] == "mglru_disabled_with_swap_pressure"


def test_classify_mglru_disabled_no_pressure():
    # Disabled but swap idle and PSI low → no alert.
    v = mod.classify(0, 0, 0, 0.0, True, True)
    assert v["verdict"] == "ok"


def test_classify_min_ttl_too_low_with_swap():
    v = mod.classify(7, 0, 500_000, 0.0, True, True)
    assert v["verdict"] == "min_ttl_too_low"


def test_classify_min_ttl_zero_no_swap_ok():
    v = mod.classify(7, 0, 0, 0.0, True, True)
    assert v["verdict"] == "ok"


def test_classify_requires_root():
    # Enabled, sane ttl, swap idle, but debugfs locked.
    v = mod.classify(7, 1000, 0, 0.0, False, True)
    assert v["verdict"] == "requires_root"


def test_classify_ok():
    v = mod.classify(7, 1000, 500_000, 0.0, True, True)
    assert v["verdict"] == "ok"


# Priority : disabled+pressure > ttl_low > requires_root
def test_priority_disabled_over_ttl_low():
    v = mod.classify(0, 0, 500_000, 0.0, True, True)
    assert v["verdict"] == "mglru_disabled_with_swap_pressure"


def test_priority_ttl_low_over_requires_root():
    v = mod.classify(7, 0, 500_000, 0.0, False, True)
    assert v["verdict"] == "min_ttl_too_low"


# PSI threshold alone (no swap usage) also triggers
# mglru_disabled.
def test_classify_psi_alone_triggers_disabled():
    v = mod.classify(0, 0, 0, 10.0, True, True)
    assert v["verdict"] == "mglru_disabled_with_swap_pressure"


# --- status integration -----------------------------------------

def test_status_unknown(tmp_path):
    out = mod.status(None,
                          str(tmp_path / "nope_lru"),
                          str(tmp_path / "nope_debug"),
                          str(tmp_path / "nope_swaps"),
                          str(tmp_path / "nope_psi"))
    assert out["ok"] is False
    assert out["verdict"]["verdict"] == "unknown"


def test_status_ok_synthetic(tmp_path):
    lru = tmp_path / "lru"; lru.mkdir()
    (lru / "enabled").write_text("0x7\n")
    (lru / "min_ttl_ms").write_text("1000\n")
    swaps = tmp_path / "swaps"
    swaps.write_text("Filename Type Size Used Priority\n"
                        "/swap.img file 1000 0 -2\n")
    psi = tmp_path / "memory"
    psi.write_text("some avg10=0 avg60=0 avg300=0 total=0\n"
                        "full avg10=0 avg60=0 avg300=0 total=0\n")
    out = mod.status(None, str(lru),
                          str(tmp_path / "no_debug"),
                          str(swaps), str(psi))
    assert out["ok"] is True
    assert out["enabled"] == 7
    assert out["verdict"]["verdict"] == "ok"
