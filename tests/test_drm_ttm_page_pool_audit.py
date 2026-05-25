"""Tests for modules/drm_ttm_page_pool_audit.py — R&D #94.3."""
from __future__ import annotations

import pytest

from gpu_dashboard.modules import drm_ttm_page_pool_audit as mod


def _mk_ttm(tmp_path, *, page_pool_size="0",
             pages_limit="0", dma32_pages_limit="0"):
    d = tmp_path / "ttm"
    d.mkdir(parents=True, exist_ok=True)
    (d / "page_pool_size").write_text(page_pool_size + "\n")
    (d / "pages_limit").write_text(pages_limit + "\n")
    (d / "dma32_pages_limit").write_text(
        dma32_pages_limit + "\n")
    return str(d)


def _mk_meminfo(tmp_path, mem_avail_kib=14_000_000):
    p = tmp_path / "meminfo"
    p.write_text(f"MemAvailable: {mem_avail_kib} kB\n")
    return str(p)


# --- parse_mem_available_bytes ---------------------------------

def test_parse_mem_available_present():
    text = "MemAvailable: 1000 kB\n"
    assert mod.parse_mem_available_bytes(text) == 1024000


def test_parse_mem_available_absent():
    assert mod.parse_mem_available_bytes(
        "MemTotal: 100 kB\n") is None


# --- read_ttm_params -------------------------------------------

def test_read_ttm_params_missing(tmp_path):
    out = mod.read_ttm_params(str(tmp_path / "nope"))
    assert out["page_pool_size"] is None


def test_read_ttm_params_full(tmp_path):
    r = _mk_ttm(tmp_path)
    out = mod.read_ttm_params(r)
    assert out["page_pool_size"] == 0
    assert out["pages_limit"] == 0


# --- classify --------------------------------------------------

def test_classify_unknown_no_ttm():
    v = mod.classify({}, False, None)
    assert v["verdict"] == "unknown"


def test_classify_uncapped_all_zero():
    v = mod.classify(
        {"page_pool_size": 0, "pages_limit": 0,
         "dma32_pages_limit": 0},
        True, 14 * 2**30)
    assert v["verdict"] == "ttm_pool_uncapped"


def test_classify_ok_pages_limit_set():
    v = mod.classify(
        {"page_pool_size": 0, "pages_limit": 1024,
         "dma32_pages_limit": 0},
        True, 14 * 2**30)
    assert v["verdict"] == "ok"


def test_classify_ok_pool_size_set():
    v = mod.classify(
        {"page_pool_size": 16384, "pages_limit": 0,
         "dma32_pages_limit": 0},
        True, 14 * 2**30)
    assert v["verdict"] == "ok"


# --- status integration ----------------------------------------

def test_status_unknown(tmp_path):
    out = mod.status(None,
                       str(tmp_path / "nope_ttm"),
                       str(tmp_path / "nope_mem"))
    assert out["verdict"]["verdict"] == "unknown"


def test_status_uncapped_synthetic(tmp_path):
    r = _mk_ttm(tmp_path)
    m = _mk_meminfo(tmp_path)
    out = mod.status(None, r, m)
    assert out["verdict"]["verdict"] == "ttm_pool_uncapped"
    assert out["ttm_present"] is True


def test_status_ok_synthetic(tmp_path):
    r = _mk_ttm(tmp_path, pages_limit="2048000")
    m = _mk_meminfo(tmp_path)
    out = mod.status(None, r, m)
    assert out["verdict"]["verdict"] == "ok"
    assert out["params"]["pages_limit"] == 2048000
