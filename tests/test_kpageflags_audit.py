"""Tests for modules/kpageflags_audit.py — R&D #69.2."""
from __future__ import annotations

import os
import struct

import pytest

from gpu_dashboard.modules import kpageflags_audit as mod


def _flag(*bits):
    v = 0
    for b in bits:
        v |= 1 << b
    return v


# --- scan_kpageflags --------------------------------------------

def test_scan_missing(tmp_path):
    out = mod.scan_kpageflags(str(tmp_path / "nope"))
    assert out["present"] is False
    assert out["readable"] is False
    assert out["pages_sampled"] == 0


def test_scan_present_empty(tmp_path):
    p = tmp_path / "kpageflags"
    p.write_bytes(b"")
    out = mod.scan_kpageflags(str(p))
    assert out["present"] is True
    assert out["readable"] is True
    assert out["pages_sampled"] == 0


def test_scan_counts_flags(tmp_path):
    p = tmp_path / "kpageflags"
    # Three pages : two with LRU+ACTIVE, one with HWPOISON
    blob = b""
    blob += struct.pack("<Q",
                              _flag(mod.KPF_LRU, mod.KPF_ACTIVE))
    blob += struct.pack("<Q",
                              _flag(mod.KPF_LRU, mod.KPF_ACTIVE))
    blob += struct.pack("<Q", _flag(mod.KPF_HWPOISON))
    p.write_bytes(blob)
    out = mod.scan_kpageflags(str(p))
    assert out["pages_sampled"] == 3
    assert out["flag_counts"]["LRU"] == 2
    assert out["flag_counts"]["ACTIVE"] == 2
    assert out["flag_counts"]["HWPOISON"] == 1


# --- classify ---------------------------------------------------

def test_classify_unknown():
    v = mod.classify({"present": False, "readable": False,
                          "pages_sampled": 0, "flag_counts": {}},
                          None)
    assert v["verdict"] == "unknown"


def test_classify_requires_root():
    v = mod.classify({"present": True, "readable": False,
                          "pages_sampled": 0, "flag_counts": {}},
                          0)
    assert v["verdict"] == "requires_root"


def test_classify_kpageflags_unreadable_with_uffd():
    v = mod.classify({"present": True, "readable": False,
                          "pages_sampled": 0, "flag_counts": {}},
                          1)
    assert v["verdict"] == "kpageflags_unreadable_no_capsys"


def test_classify_ok():
    v = mod.classify({"present": True, "readable": True,
                          "pages_sampled": 1000,
                          "flag_counts": {"LRU": 800,
                                             "ACTIVE": 600,
                                             "COMPOUND_HEAD": 10,
                                             "COMPOUND_TAIL":
                                                 2000}},
                          0)
    assert v["verdict"] == "ok"


def test_classify_hwpoison():
    v = mod.classify({"present": True, "readable": True,
                          "pages_sampled": 1000,
                          "flag_counts": {"HWPOISON": 1}},
                          0)
    assert v["verdict"] == "excess_unevictable_or_hwpoison"


def test_classify_excess_unevictable():
    v = mod.classify({"present": True, "readable": True,
                          "pages_sampled": 1000,
                          "flag_counts": {"UNEVICTABLE": 400}},
                          0)
    assert v["verdict"] == "excess_unevictable_or_hwpoison"


def test_classify_unevictable_under_threshold_ok():
    v = mod.classify({"present": True, "readable": True,
                          "pages_sampled": 1000,
                          "flag_counts": {"UNEVICTABLE": 50}},
                          0)
    assert v["verdict"] == "ok"


def test_classify_compound_fragmentation():
    # 10 COMPOUND_HEAD but only 50 COMPOUND_TAIL → ratio 5 < 100
    v = mod.classify({"present": True, "readable": True,
                          "pages_sampled": 5000,
                          "flag_counts": {
                              "COMPOUND_HEAD": 10,
                              "COMPOUND_TAIL": 50}},
                          0)
    assert v["verdict"] == "high_compound_fragmentation"


def test_classify_compound_few_heads_skipped():
    # Only 2 heads → too small to draw conclusion.
    v = mod.classify({"present": True, "readable": True,
                          "pages_sampled": 5000,
                          "flag_counts": {
                              "COMPOUND_HEAD": 2,
                              "COMPOUND_TAIL": 4}},
                          0)
    assert v["verdict"] == "ok"


# Priority : hwpoison > compound_frag
def test_priority_hwpoison_over_compound():
    v = mod.classify({"present": True, "readable": True,
                          "pages_sampled": 1000,
                          "flag_counts": {
                              "HWPOISON": 1,
                              "COMPOUND_HEAD": 10,
                              "COMPOUND_TAIL": 5}},
                          0)
    assert v["verdict"] == "excess_unevictable_or_hwpoison"


# --- status integration -----------------------------------------

def test_status_unknown(tmp_path):
    out = mod.status(None, str(tmp_path / "nope"),
                          str(tmp_path / "nocount"),
                          str(tmp_path / "no_bd"),
                          str(tmp_path / "no_uffd"))
    assert out["ok"] is False
    assert out["verdict"]["verdict"] == "unknown"


def test_status_synthetic_ok(tmp_path):
    kpf = tmp_path / "kpageflags"
    blob = b""
    for _ in range(1000):
        blob += struct.pack("<Q",
                                  _flag(mod.KPF_LRU,
                                          mod.KPF_ACTIVE))
    kpf.write_bytes(blob)
    out = mod.status(None, str(kpf),
                          str(tmp_path / "no_count"),
                          str(tmp_path / "no_bd"),
                          str(tmp_path / "no_uffd"))
    assert out["ok"] is True
    assert out["pages_sampled"] == 1000
    assert out["verdict"]["verdict"] == "ok"
