"""Tests for modules/entropy_audit.py — R&D #45.4."""
from __future__ import annotations

from pathlib import Path

import pytest

from gpu_dashboard.modules import entropy_audit as mod


# --- read_random ---------------------------------------------------

def test_read_random_missing(tmp_path):
    assert mod.read_random(str(tmp_path / "nope")) == {}


def test_read_random_basic(tmp_path):
    root = tmp_path / "r"
    root.mkdir()
    (root / "entropy_avail").write_text("256\n")
    (root / "poolsize").write_text("256\n")
    (root / "urandom_min_reseed_secs").write_text("60\n")
    out = mod.read_random(str(root))
    assert out["entropy_avail"] == 256
    assert out["poolsize"] == 256


def test_read_random_partial_ok(tmp_path):
    root = tmp_path / "r"
    root.mkdir()
    (root / "entropy_avail").write_text("100\n")
    out = mod.read_random(str(root))
    assert out == {"entropy_avail": 100}


# --- read_hwrng ----------------------------------------------------

def test_read_hwrng_missing(tmp_path):
    out = mod.read_hwrng(str(tmp_path / "nope"))
    assert out == {"available": False}


def test_read_hwrng_with_virtio(tmp_path):
    root = tmp_path / "hw"
    root.mkdir()
    (root / "rng_current").write_text("virtio_rng.0\n")
    (root / "rng_available").write_text("virtio_rng.0 tpm-rng-0\n")
    (root / "rng_quality").write_text("700\n")
    out = mod.read_hwrng(str(root))
    assert out["available"] is True
    assert out["current"] == "virtio_rng.0"
    assert out["available_list"] == ["virtio_rng.0", "tpm-rng-0"]
    assert out["quality"] == 700


def test_read_hwrng_none(tmp_path):
    root = tmp_path / "hw"
    root.mkdir()
    (root / "rng_current").write_text("none\n")
    out = mod.read_hwrng(str(root))
    assert out["current"] == "none"


# --- classify ------------------------------------------------------

def test_classify_unknown_when_empty():
    v = mod.classify({}, {})
    assert v["verdict"] == "unknown"


def test_classify_no_hwrng():
    rand = {"entropy_avail": 256, "poolsize": 256}
    hw = {"available": True, "current": "none",
            "available_list": []}
    v = mod.classify(rand, hw)
    assert v["verdict"] == "no_hwrng"
    assert "rngd" in v["recommendation"]


def test_classify_no_hwrng_missing_current():
    rand = {"entropy_avail": 256, "poolsize": 256}
    hw = {"available": True, "current": None,
            "available_list": []}
    v = mod.classify(rand, hw)
    assert v["verdict"] == "no_hwrng"


def test_classify_low_entropy():
    rand = {"entropy_avail": 50, "poolsize": 4096}  # < 25 %
    hw = {"available": True, "current": "virtio_rng.0",
            "available_list": ["virtio_rng.0"]}
    v = mod.classify(rand, hw)
    assert v["verdict"] == "low_entropy"


def test_classify_ok_with_hwrng():
    rand = {"entropy_avail": 4000, "poolsize": 4096}
    hw = {"available": True, "current": "virtio_rng.0",
            "available_list": ["virtio_rng.0"]}
    v = mod.classify(rand, hw)
    assert v["verdict"] == "ok"


def test_classify_ok_modern_kernel():
    # Modern kernel : entropy_avail always == poolsize.
    rand = {"entropy_avail": 256, "poolsize": 256}
    hw = {"available": True, "current": "tpm-rng-0",
            "available_list": ["tpm-rng-0"]}
    v = mod.classify(rand, hw)
    assert v["verdict"] == "ok"


def test_classify_no_hwrng_wins_over_low_entropy():
    rand = {"entropy_avail": 50, "poolsize": 4096}
    hw = {"available": True, "current": "none"}
    v = mod.classify(rand, hw)
    assert v["verdict"] == "no_hwrng"


def test_classify_low_entropy_skipped_when_hwrng_unavailable():
    # /sys/class/misc/hw_random missing entirely → don't flag
    # no_hwrng (kernel CONFIG_HW_RANDOM=n).
    rand = {"entropy_avail": 256, "poolsize": 256}
    hw = {"available": False}
    v = mod.classify(rand, hw)
    assert v["verdict"] == "ok"


# --- status integration -------------------------------------------

def test_status_with_isolated_roots(monkeypatch, tmp_path):
    rdir = tmp_path / "random"
    rdir.mkdir()
    (rdir / "entropy_avail").write_text("256\n")
    (rdir / "poolsize").write_text("256\n")
    hdir = tmp_path / "hwrng"
    hdir.mkdir()
    (hdir / "rng_current").write_text("none\n")
    (hdir / "rng_available").write_text("\n")
    monkeypatch.setattr(mod, "_PROC_SYS_RANDOM", str(rdir))
    monkeypatch.setattr(mod, "_SYS_HWRNG", str(hdir))
    out = mod.status()
    assert out["ok"] is True
    assert out["verdict"]["verdict"] == "no_hwrng"


def test_status_unknown(monkeypatch, tmp_path):
    monkeypatch.setattr(mod, "_PROC_SYS_RANDOM",
                        str(tmp_path / "nope"))
    monkeypatch.setattr(mod, "_SYS_HWRNG",
                        str(tmp_path / "nope2"))
    out = mod.status()
    assert out["ok"] is False
    assert out["verdict"]["verdict"] == "unknown"
