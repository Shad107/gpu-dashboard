"""Tests for modules/zfs_arc_audit.py — R&D #97.2."""
from __future__ import annotations

import pytest

from gpu_dashboard.modules import zfs_arc_audit as mod


_ARCSTATS_BASE = (
    "12 1 0x01 0 0 0 0\n"
    "name                            type data\n"
    "hits                            4    1000\n"
    "misses                          4    100\n"
    "size                            4    1073741824\n"
    "c_min                           4    268435456\n"
    "c_max                           4    8589934592\n"
    "arc_meta_used                   4    104857600\n"
    "arc_meta_limit                  4    1073741824\n"
)


def _mk_arcstats(tmp_path, text=_ARCSTATS_BASE,
                  name="arcstats"):
    p = tmp_path / name
    p.write_text(text)
    return str(p)


def _mk_params(tmp_path, *, zfs_arc_max="0",
                 zfs_arc_min="268435456"):
    d = tmp_path / "zfs_params"
    d.mkdir(parents=True, exist_ok=True)
    if zfs_arc_max is not None:
        (d / "zfs_arc_max").write_text(zfs_arc_max + "\n")
    if zfs_arc_min is not None:
        (d / "zfs_arc_min").write_text(zfs_arc_min + "\n")
    return str(d)


def _mk_meminfo(tmp_path, mem_kib=64 * 2**20):
    p = tmp_path / "meminfo"
    p.write_text(f"MemTotal: {mem_kib} kB\n")
    return str(p)


# --- parse_arcstats --------------------------------------------

def test_parse_arcstats_empty():
    assert mod.parse_arcstats("") == {}


def test_parse_arcstats_typical():
    out = mod.parse_arcstats(_ARCSTATS_BASE)
    assert out["size"] == 1073741824
    assert out["c_max"] == 8589934592
    assert out["arc_meta_used"] == 104857600


def test_parse_arcstats_ignores_garbage():
    text = "garbage row\nhits 4 5\n"
    out = mod.parse_arcstats(text)
    assert out["hits"] == 5


# --- classify --------------------------------------------------

_GIB = 2**30


def test_classify_unknown_no_zfs():
    v = mod.classify(False, False, {}, {}, 64 * _GIB)
    assert v["verdict"] == "unknown"


def test_classify_requires_root():
    v = mod.classify(True, False, {}, {}, 64 * _GIB)
    assert v["verdict"] == "requires_root"


def test_classify_unbounded_err():
    # zfs_arc_max=0 on a 64 GiB box
    v = mod.classify(
        True, True,
        {"size": 1 * _GIB, "c_max": 32 * _GIB},
        {"zfs_arc_max": 0, "zfs_arc_min": 1 * _GIB},
        64 * _GIB)
    assert v["verdict"] == "arc_unbounded"


def test_classify_unbounded_skipped_on_small_box():
    # zfs_arc_max=0 but only 16 GiB → not big enough
    v = mod.classify(
        True, True,
        {"size": 1 * _GIB},
        {"zfs_arc_max": 0},
        16 * _GIB)
    # Doesn't trip arc_unbounded (size < 32 GiB threshold)
    assert v["verdict"] == "ok"


def test_classify_arc_eating_ram_warn():
    # Size = 35 GiB on 64 GiB box (> 50%)
    v = mod.classify(
        True, True,
        {"size": 35 * _GIB},
        {"zfs_arc_max": 40 * _GIB},
        64 * _GIB)
    assert v["verdict"] == "arc_eating_ram"


def test_classify_meta_pressure_accent():
    # meta_used = 95% of meta_limit
    v = mod.classify(
        True, True,
        {"size": 1 * _GIB,
         "arc_meta_used": 950_000_000,
         "arc_meta_limit": 1_000_000_000},
        {"zfs_arc_max": 8 * _GIB},
        64 * _GIB)
    assert v["verdict"] == "arc_meta_pressure"


def test_classify_ok():
    v = mod.classify(
        True, True,
        {"size": 1 * _GIB,
         "arc_meta_used": 100_000_000,
         "arc_meta_limit": 1_000_000_000},
        {"zfs_arc_max": 8 * _GIB},
        64 * _GIB)
    assert v["verdict"] == "ok"


# Priority : unbounded > eating > meta
def test_priority_unbounded_over_eating():
    v = mod.classify(
        True, True,
        {"size": 35 * _GIB},
        {"zfs_arc_max": 0},
        64 * _GIB)
    assert v["verdict"] == "arc_unbounded"


def test_priority_eating_over_meta():
    v = mod.classify(
        True, True,
        {"size": 35 * _GIB,
         "arc_meta_used": 950_000_000,
         "arc_meta_limit": 1_000_000_000},
        {"zfs_arc_max": 40 * _GIB},
        64 * _GIB)
    assert v["verdict"] == "arc_eating_ram"


# --- status integration ----------------------------------------

def test_status_unknown(tmp_path):
    out = mod.status(None,
                       str(tmp_path / "nope_arc"),
                       str(tmp_path / "nope_params"),
                       str(tmp_path / "nope_mem"))
    assert out["verdict"]["verdict"] == "unknown"


def test_status_ok_synthetic(tmp_path):
    a = _mk_arcstats(tmp_path)
    p = _mk_params(tmp_path, zfs_arc_max=str(8 * _GIB))
    m = _mk_meminfo(tmp_path)
    out = mod.status(None, a, p, m)
    assert out["verdict"]["verdict"] == "ok"
    assert out["zfs_loaded"] is True


def test_status_unbounded_synthetic(tmp_path):
    a = _mk_arcstats(tmp_path)
    p = _mk_params(tmp_path, zfs_arc_max="0")
    m = _mk_meminfo(tmp_path, mem_kib=64 * 2**20)
    out = mod.status(None, a, p, m)
    assert out["verdict"]["verdict"] == "arc_unbounded"
    assert out["ok"] is False
