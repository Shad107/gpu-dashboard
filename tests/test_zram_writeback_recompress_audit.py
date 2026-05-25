"""Tests for modules/zram_writeback_recompress_audit.py R&D #103.3."""
from __future__ import annotations

import pytest

from gpu_dashboard.modules import zram_writeback_recompress_audit as mod


def _mk_zram(root, name, *, disksize=1_073_741_824,
               backing_dev="none",
               recomp_algorithm="zstd",
               mm_stat="100000 50000 60000 0 70000 0 0\n",
               bd_stat="0 0 0\n"):
    d = root / name
    d.mkdir(parents=True, exist_ok=True)
    (d / "disksize").write_text(str(disksize) + "\n")
    (d / "backing_dev").write_text(backing_dev + "\n")
    (d / "recomp_algorithm").write_text(recomp_algorithm + "\n")
    (d / "mm_stat").write_text(mm_stat)
    (d / "bd_stat").write_text(bd_stat)


# --- parse_mm_stat ---------------------------------------------

def test_parse_mm_stat_empty():
    assert mod.parse_mm_stat("") == {}


def test_parse_mm_stat_full():
    out = mod.parse_mm_stat(
        "1000 500 600 0 700 0 0\n")
    assert out["orig_size"] == 1000
    assert out["compr_size"] == 500
    assert out["mem_used_total"] == 600


# --- parse_bd_stat ---------------------------------------------

def test_parse_bd_stat_empty():
    assert mod.parse_bd_stat("") == {}


def test_parse_bd_stat_basic():
    out = mod.parse_bd_stat("10 20 30\n")
    assert out == {"reads": 10, "writes": 20, "total": 30}


# --- walk_zram -------------------------------------------------

def test_walk_zram_missing(tmp_path):
    assert mod.walk_zram(str(tmp_path / "nope")) == []


def test_walk_zram_basic(tmp_path):
    _mk_zram(tmp_path, "zram0")
    out = mod.walk_zram(str(tmp_path))
    assert len(out) == 1
    assert out[0]["name"] == "zram0"


# --- classify --------------------------------------------------

def _z(*, name="zram0", disksize=1_000_000,
       backing_dev="none", recomp="zstd",
       compr_size=0, bd_writes=0, bd_total=0):
    return {"name": name, "disksize": disksize,
            "backing_dev": backing_dev,
            "recomp_algorithm": recomp,
            "mm_stat": {"compr_size": compr_size},
            "bd_stat": {"reads": 0, "writes": bd_writes,
                          "total": bd_total}}


def test_classify_unknown_no_zrams():
    v = mod.classify([], True)
    assert v["verdict"] == "unknown"


def test_classify_requires_root():
    v = mod.classify([_z()], False)
    assert v["verdict"] == "requires_root"


def test_classify_ok_idle():
    v = mod.classify([_z()], True)
    assert v["verdict"] == "ok"


def test_classify_backing_broken_err():
    v = mod.classify(
        [_z(backing_dev="259:3", compr_size=500_000,
            bd_writes=0, bd_total=100)],
        True)
    assert v["verdict"] == "backing_dev_pipeline_broken"


def test_classify_zram_full_no_backing_warn():
    v = mod.classify(
        [_z(disksize=1_000_000,
            compr_size=600_000,
            backing_dev="none")],
        True)
    assert v["verdict"] == "zram_full_no_backing"


def test_classify_recomp_unset_accent():
    v = mod.classify(
        [_z(recomp="")], True)
    assert v["verdict"] == "recomp_unset"


def test_classify_recomp_none_accent():
    z = _z()
    z["recomp_algorithm"] = None
    v = mod.classify([z], True)
    assert v["verdict"] == "recomp_unset"


# Priority : broken > full > recomp
def test_priority_broken_over_full():
    v = mod.classify(
        [_z(backing_dev="259:3", compr_size=600_000,
            disksize=1_000_000, bd_writes=0,
            bd_total=100)],
        True)
    assert v["verdict"] == "backing_dev_pipeline_broken"


def test_priority_full_over_recomp():
    v = mod.classify(
        [_z(backing_dev="none",
            compr_size=600_000,
            disksize=1_000_000, recomp="")],
        True)
    assert v["verdict"] == "zram_full_no_backing"


# --- status integration ----------------------------------------

def test_status_unknown(tmp_path):
    out = mod.status(None, str(tmp_path / "nope"))
    assert out["verdict"]["verdict"] == "unknown"


def test_status_ok_synthetic(tmp_path):
    _mk_zram(tmp_path, "zram0")
    out = mod.status(None, str(tmp_path))
    assert out["verdict"]["verdict"] == "ok"
    assert out["zram_count"] == 1
