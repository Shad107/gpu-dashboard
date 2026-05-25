"""Tests for modules/cgroup_v2_uclamp_audit.py R&D #103.4."""
from __future__ import annotations

import pytest

from gpu_dashboard.modules import cgroup_v2_uclamp_audit as mod


def _mk_v2_root(tmp_path, controllers="cpu memory"):
    d = tmp_path / "cgroup"
    d.mkdir(parents=True, exist_ok=True)
    (d / "cgroup.controllers").write_text(controllers + "\n")
    return d


def _mk_slice(root, name, *, uclamp_min="0", uclamp_max="max",
                zswap_max="max", zswap_writeback="1"):
    d = root / name
    d.mkdir(parents=True, exist_ok=True)
    (d / "cpu.uclamp.min").write_text(uclamp_min + "\n")
    (d / "cpu.uclamp.max").write_text(uclamp_max + "\n")
    (d / "memory.zswap.max").write_text(zswap_max + "\n")
    (d / "memory.zswap.writeback").write_text(
        zswap_writeback + "\n")


# --- parse_uclamp ----------------------------------------------

def test_parse_uclamp_max():
    assert mod.parse_uclamp("max") == 100.0


def test_parse_uclamp_pct():
    assert mod.parse_uclamp("50.5") == 50.5


def test_parse_uclamp_none():
    assert mod.parse_uclamp(None) is None
    assert mod.parse_uclamp("garbage") is None


# --- walk_slices -----------------------------------------------

def test_walk_slices_empty(tmp_path):
    out = mod.walk_slices(str(tmp_path / "nope"))
    assert out == []


def test_walk_slices_basic(tmp_path):
    cg = _mk_v2_root(tmp_path)
    _mk_slice(cg, "user.slice")
    _mk_slice(cg, "system.slice")
    out = mod.walk_slices(str(cg))
    assert len(out) == 2
    paths = {s["path"] for s in out}
    assert paths == {"user.slice", "system.slice"}


# --- classify --------------------------------------------------

def _s(*, path="user.slice", umin=0.0, umax=100.0,
       zmax="max", zwb=1):
    return {"path": path,
            "uclamp_min": umin,
            "uclamp_max": umax,
            "zswap_max": zmax,
            "zswap_writeback": zwb}


def test_classify_unknown():
    v = mod.classify(False, False, [])
    assert v["verdict"] == "unknown"


def test_classify_requires_root():
    v = mod.classify(True, False, [])
    assert v["verdict"] == "requires_root"


def test_classify_unknown_no_slices():
    v = mod.classify(True, True, [])
    assert v["verdict"] == "unknown"


def test_classify_ok():
    v = mod.classify(True, True, [_s()])
    assert v["verdict"] == "ok"


def test_classify_uclamp_max_err():
    v = mod.classify(True, True, [_s(umax=50.0)])
    assert v["verdict"] == "uclamp_max_below_100"


def test_classify_zswap_zero_warn():
    v = mod.classify(True, True, [_s(zmax="0")])
    assert v["verdict"] == "zswap_disabled_on_slice"


def test_classify_uclamp_min_accent():
    v = mod.classify(True, True, [_s(umin=20.0)])
    assert v["verdict"] == "uclamp_min_boosted"


def test_classify_zswap_writeback_off_accent():
    v = mod.classify(True, True, [_s(zwb=0)])
    assert v["verdict"] == "zswap_writeback_off"


# Priority : umax > zmax=0 > umin > zwb
def test_priority_umax_over_zmax():
    v = mod.classify(True, True,
                          [_s(umax=50.0, zmax="0")])
    assert v["verdict"] == "uclamp_max_below_100"


def test_priority_zmax_over_umin():
    v = mod.classify(True, True,
                          [_s(zmax="0", umin=20.0)])
    assert v["verdict"] == "zswap_disabled_on_slice"


# --- status integration ----------------------------------------

def test_status_unknown(tmp_path):
    out = mod.status(None, str(tmp_path / "nope"))
    assert out["verdict"]["verdict"] == "unknown"


def test_status_ok_synthetic(tmp_path):
    cg = _mk_v2_root(tmp_path)
    _mk_slice(cg, "user.slice")
    _mk_slice(cg, "system.slice")
    out = mod.status(None, str(cg))
    assert out["verdict"]["verdict"] == "ok"
    assert out["slice_count"] == 2


def test_status_uclamp_capped(tmp_path):
    cg = _mk_v2_root(tmp_path)
    _mk_slice(cg, "user.slice", uclamp_max="50")
    _mk_slice(cg, "system.slice")
    out = mod.status(None, str(cg))
    assert (out["verdict"]["verdict"]
            == "uclamp_max_below_100")
