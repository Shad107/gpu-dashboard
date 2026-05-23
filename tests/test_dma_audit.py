"""Tests for modules/dma_audit.py — R&D #48.3."""
from __future__ import annotations

import pytest

from gpu_dashboard.modules import dma_audit as mod


# --- list_dma_engines --------------------------------------------

def test_list_dma_engines_empty(tmp_path):
    sysdma = tmp_path / "dma"
    sysdma.mkdir()
    assert mod.list_dma_engines(str(sysdma)) == []


def test_list_dma_engines_missing(tmp_path):
    assert mod.list_dma_engines(str(tmp_path / "nope")) == []


def test_list_dma_engines_basic(tmp_path):
    sysdma = tmp_path / "dma"
    sysdma.mkdir()
    (sysdma / "dma0chan0").mkdir()
    (sysdma / "dma0chan0" / "bytes_transferred").write_text("1234\n")
    (sysdma / "dma0chan0" / "memcpy_count").write_text("567\n")
    out = mod.list_dma_engines(str(sysdma))
    assert len(out) == 1
    assert out[0]["name"] == "dma0chan0"
    assert out[0]["bytes_transferred"] == 1234
    assert out[0]["memcpy_count"] == 567


# --- read_swiotlb -------------------------------------------------

def test_read_swiotlb_missing(tmp_path):
    out = mod.read_swiotlb(str(tmp_path / "nope"))
    assert out == {"available": False, "permission_error": False}


def test_read_swiotlb_present(tmp_path):
    swdir = tmp_path / "sw"
    swdir.mkdir()
    (swdir / "io_tlb_nslabs").write_text("65536\n")
    (swdir / "io_tlb_used").write_text("100\n")
    out = mod.read_swiotlb(str(swdir))
    assert out["available"] is True
    assert out["io_tlb_nslabs"] == 65536
    assert out["io_tlb_used"] == 100
    assert 0.001 < out["used_ratio"] < 0.01


# --- classify ----------------------------------------------------

def test_classify_no_dma_devices():
    v = mod.classify([], {"available": False})
    assert v["verdict"] == "no_dma_devices"


def test_classify_ok_engines_only():
    v = mod.classify([{"name": "dma0", "bytes_transferred": 0,
                          "in_use": None, "memcpy_count": 0}],
                       {"available": False})
    assert v["verdict"] == "ok"


def test_classify_swiotlb_bounce_high():
    v = mod.classify([], {"available": True,
                            "io_tlb_nslabs": 65536,
                            "io_tlb_used": 60000,
                            "used_ratio": 60000/65536})
    assert v["verdict"] == "swiotlb_bounce_high"
    assert "swiotlb" in v["recommendation"]


def test_classify_swiotlb_low_usage_ok():
    v = mod.classify([], {"available": True,
                            "io_tlb_nslabs": 65536,
                            "io_tlb_used": 100,
                            "used_ratio": 100/65536})
    assert v["verdict"] == "ok"


def test_classify_priority_bounce_wins():
    v = mod.classify([{"name": "dma0", "bytes_transferred": 0,
                          "in_use": None, "memcpy_count": 0}],
                       {"available": True,
                        "io_tlb_nslabs": 65536,
                        "io_tlb_used": 60000,
                        "used_ratio": 60000/65536})
    assert v["verdict"] == "swiotlb_bounce_high"


# --- status integration ------------------------------------------

def test_status_no_class_dma(monkeypatch, tmp_path):
    monkeypatch.setattr(mod, "_SYS_CLASS_DMA",
                        str(tmp_path / "nope"))
    monkeypatch.setattr(mod, "_DEBUGFS_SWIOTLB",
                        str(tmp_path / "nope2"))
    out = mod.status()
    assert out["ok"] is False
    assert out["verdict"]["verdict"] == "unknown"


def test_status_empty(monkeypatch, tmp_path):
    sysdma = tmp_path / "dma"
    sysdma.mkdir()
    monkeypatch.setattr(mod, "_SYS_CLASS_DMA", str(sysdma))
    monkeypatch.setattr(mod, "_DEBUGFS_SWIOTLB",
                        str(tmp_path / "nope"))
    out = mod.status()
    assert out["ok"] is True
    assert out["dma_engine_count"] == 0
    assert out["verdict"]["verdict"] == "no_dma_devices"


def test_status_with_swiotlb(monkeypatch, tmp_path):
    sysdma = tmp_path / "dma"
    sysdma.mkdir()
    swdir = tmp_path / "sw"
    swdir.mkdir()
    (swdir / "io_tlb_nslabs").write_text("65536\n")
    (swdir / "io_tlb_used").write_text("55000\n")
    monkeypatch.setattr(mod, "_SYS_CLASS_DMA", str(sysdma))
    monkeypatch.setattr(mod, "_DEBUGFS_SWIOTLB", str(swdir))
    out = mod.status()
    assert out["ok"] is True
    assert out["verdict"]["verdict"] == "swiotlb_bounce_high"
