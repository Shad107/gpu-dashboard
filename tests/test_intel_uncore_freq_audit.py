"""Tests for modules/intel_uncore_freq_audit.py R&D #102.1."""
from __future__ import annotations

import pytest

from gpu_dashboard.modules import intel_uncore_freq_audit as mod


def _mk_die(root, name, *, min_khz=800000, max_khz=4500000,
              current_khz=4500000, init_min=800000,
              init_max=4500000):
    d = root / name
    d.mkdir(parents=True, exist_ok=True)
    (d / "min_freq_khz").write_text(f"{min_khz}\n")
    (d / "max_freq_khz").write_text(f"{max_khz}\n")
    (d / "current_freq_khz").write_text(f"{current_khz}\n")
    (d / "initial_min_freq_khz").write_text(f"{init_min}\n")
    (d / "initial_max_freq_khz").write_text(f"{init_max}\n")


# --- walk_dies -------------------------------------------------

def test_walk_missing(tmp_path):
    assert mod.walk_dies(str(tmp_path / "nope")) == []


def test_walk_basic(tmp_path):
    _mk_die(tmp_path, "package_00_die_00")
    _mk_die(tmp_path, "package_01_die_00",
                max_khz=3000000)
    out = mod.walk_dies(str(tmp_path))
    assert len(out) == 2
    names = {d["name"] for d in out}
    assert "package_00_die_00" in names


def test_walk_skips_non_die_dirs(tmp_path):
    _mk_die(tmp_path, "package_00_die_00")
    (tmp_path / "non_die_subdir").mkdir()
    out = mod.walk_dies(str(tmp_path))
    assert len(out) == 1


# --- classify --------------------------------------------------

def _d(*, name="package_00_die_00", min_khz=800000,
       max_khz=4500000, current_khz=4500000,
       init_max=4500000):
    return {"name": name,
            "min_freq_khz": min_khz,
            "max_freq_khz": max_khz,
            "current_freq_khz": current_khz,
            "initial_min_freq_khz": 800000,
            "initial_max_freq_khz": init_max}


def test_classify_unknown_no_driver():
    v = mod.classify(False, [], False)
    assert v["verdict"] == "unknown"


def test_classify_requires_root():
    v = mod.classify(True, [], False)
    assert v["verdict"] == "requires_root"


def test_classify_no_dies_unknown():
    v = mod.classify(True, [], True)
    assert v["verdict"] == "unknown"


def test_classify_ok():
    v = mod.classify(True, [_d()], True)
    assert v["verdict"] == "ok"


def test_classify_hard_clamp_err():
    # max=3 GHz vs initial 4.5 GHz → 3.0/4.5 = 0.67 < 0.7
    v = mod.classify(True,
                          [_d(max_khz=3000000)], True)
    assert v["verdict"] == "uncore_max_clamped_hard"


def test_classify_stuck_at_min_warn():
    v = mod.classify(True,
                          [_d(current_khz=800000)], True)
    assert v["verdict"] == "uncore_stuck_at_min"


def test_classify_soft_clamp_accent():
    # max=4.0 GHz vs init 4.5 GHz → ratio 0.89 (soft)
    v = mod.classify(True,
                          [_d(max_khz=4000000)], True)
    assert v["verdict"] == "uncore_max_clamped_soft"


# Priority : hard > stuck > soft
def test_priority_hard_over_stuck():
    v = mod.classify(
        True,
        [_d(max_khz=3000000, current_khz=800000)],
        True)
    assert v["verdict"] == "uncore_max_clamped_hard"


def test_priority_stuck_over_soft():
    # max=4.0 GHz (soft) AND current=800kHz=min (stuck)
    v = mod.classify(
        True,
        [_d(max_khz=4000000, current_khz=800000)],
        True)
    assert v["verdict"] == "uncore_stuck_at_min"


# --- status integration ----------------------------------------

def test_status_unknown(tmp_path):
    out = mod.status(None, str(tmp_path / "nope"))
    assert out["verdict"]["verdict"] == "unknown"


def test_status_ok_synthetic(tmp_path):
    root = tmp_path / "uncore"
    root.mkdir()
    _mk_die(root, "package_00_die_00")
    out = mod.status(None, str(root))
    assert out["verdict"]["verdict"] == "ok"
    assert out["die_count"] == 1


def test_status_hard_clamp_synthetic(tmp_path):
    root = tmp_path / "uncore"
    root.mkdir()
    _mk_die(root, "package_00_die_00",
                max_khz=2_500_000,
                init_max=4_500_000)
    out = mod.status(None, str(root))
    assert (out["verdict"]["verdict"]
            == "uncore_max_clamped_hard")
