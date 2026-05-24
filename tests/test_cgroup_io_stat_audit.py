"""Tests for modules/cgroup_io_stat_audit.py — R&D #81.3."""
from __future__ import annotations

import os

import pytest

from gpu_dashboard.modules import cgroup_io_stat_audit as mod


def _mk_cgroup(root, rel_path, *, rbytes=0, wbytes=0,
                dbytes=0, rios=0, wios=0,
                pressure_full_avg10=0.0,
                pressure_full_avg300=0.0,
                pressure=True):
    """Create /sys/fs/cgroup/<rel_path>/io.stat + optional
    io.pressure file."""
    d = root / rel_path.lstrip("/")
    d.mkdir(parents=True, exist_ok=True)
    (d / "io.stat").write_text(
        f"8:0 rbytes={rbytes} wbytes={wbytes} "
        f"rios={rios} wios={wios} "
        f"dbytes={dbytes} dios=0\n")
    if pressure:
        (d / "io.pressure").write_text(
            f"some avg10=0.00 avg60=0.00 avg300=0.00 total=0\n"
            f"full avg10={pressure_full_avg10:.2f} "
            f"avg60=0.00 avg300={pressure_full_avg300:.2f} "
            f"total=0\n")


def _mk_v2_root(tmp_path, with_io=True):
    """Mark tmp_path as a cgroup v2 root."""
    controllers = "cpu io memory pids" if with_io else "cpu memory"
    (tmp_path / "cgroup.controllers").write_text(
        controllers + "\n")


# --- parse_io_stat ---------------------------------------------

def test_parse_io_stat_empty():
    assert mod.parse_io_stat("") == {}


def test_parse_io_stat_one_dev():
    out = mod.parse_io_stat(
        "8:0 rbytes=1000 wbytes=2000 rios=5 wios=6 "
        "dbytes=0 dios=0\n")
    assert out["8:0"]["rbytes"] == 1000
    assert out["8:0"]["wbytes"] == 2000


def test_parse_io_stat_multi_dev():
    out = mod.parse_io_stat(
        "8:0 rbytes=1000 wbytes=2000 rios=1 wios=1 dbytes=0 dios=0\n"
        "259:0 rbytes=500 wbytes=300 rios=1 wios=1 dbytes=0 dios=0\n")
    assert len(out) == 2
    assert out["259:0"]["wbytes"] == 300


# --- parse_pressure --------------------------------------------

def test_parse_pressure():
    text = ("some avg10=1.23 avg60=2.34 avg300=3.45 total=100\n"
            "full avg10=10.50 avg60=20.00 avg300=30.10 total=200\n")
    out = mod.parse_pressure(text)
    assert out["some"]["avg10"] == 1.23
    assert out["full"]["avg300"] == 30.10
    assert out["full"]["total"] == 200


def test_parse_pressure_empty():
    assert mod.parse_pressure("") == {}


# --- is_cgroup_v2 ----------------------------------------------

def test_is_cgroup_v2_yes(tmp_path):
    _mk_v2_root(tmp_path, with_io=True)
    assert mod.is_cgroup_v2(str(tmp_path)) is True


def test_is_cgroup_v2_no_io(tmp_path):
    _mk_v2_root(tmp_path, with_io=False)
    assert mod.is_cgroup_v2(str(tmp_path)) is False


def test_is_cgroup_v2_no_controllers(tmp_path):
    assert mod.is_cgroup_v2(str(tmp_path)) is False


# --- walk_io_stats ---------------------------------------------

def test_walk_missing(tmp_path):
    assert mod.walk_io_stats(str(tmp_path / "nope")) == []


def test_walk_finds_stats(tmp_path):
    _mk_cgroup(tmp_path, "/", wbytes=100)
    _mk_cgroup(tmp_path, "system.slice", wbytes=50)
    _mk_cgroup(tmp_path, "user.slice", wbytes=200)
    rows = mod.walk_io_stats(str(tmp_path))
    paths = sorted(r["path"] for r in rows)
    assert "/" in paths
    assert "/system.slice" in paths
    assert "/user.slice" in paths


def test_walk_aggregates_totals(tmp_path):
    _mk_cgroup(tmp_path, "/", wbytes=1000, rbytes=500)
    rows = mod.walk_io_stats(str(tmp_path))
    root = next(r for r in rows if r["path"] == "/")
    assert root["totals"]["wbytes"] == 1000
    assert root["totals"]["rbytes"] == 500


# --- classify --------------------------------------------------

def test_classify_unknown_no_root():
    v = mod.classify(False, None, [], False)
    assert v["verdict"] == "unknown"


def test_classify_no_cgroup_v2():
    v = mod.classify(False, None, [], True)
    assert v["verdict"] == "no_cgroup_v2"


def test_classify_unknown_no_stats():
    v = mod.classify(True, None, [], True)
    assert v["verdict"] == "unknown"


def _row(path, wbytes=0, rbytes=0):
    return {"path": path,
            "totals": {"wbytes": wbytes, "rbytes": rbytes,
                          "dbytes": 0, "rios": 0, "wios": 0}}


def _pressure(avg10=0.0, avg300=0.0):
    return {"some": {"avg10": 0.0, "avg60": 0.0,
                       "avg300": 0.0, "total": 0},
              "full": {"avg10": avg10, "avg60": 0.0,
                         "avg300": avg300, "total": 0}}


def test_classify_ok_balanced():
    cgroups = [
        _row("/", wbytes=1_000_000_000),
        _row("/system.slice", wbytes=500_000_000),
        _row("/user.slice", wbytes=500_000_000),
    ]
    v = mod.classify(True, _pressure(0.0, 0.0), cgroups, True)
    assert v["verdict"] == "ok_balanced"


def test_classify_runaway_writer():
    # 90% of writes in user.slice, full avg10 = 50%
    cgroups = [
        _row("/", wbytes=10 * 1024**3),
        _row("/user.slice", wbytes=9 * 1024**3),
        _row("/system.slice", wbytes=1 * 1024**3),
    ]
    v = mod.classify(
        True, _pressure(50.0, 30.0), cgroups, True)
    assert v["verdict"] == "runaway_writer"
    assert v["top_writer"] == "/user.slice"


def test_classify_runaway_below_pressure_falls_through():
    # 90% dominance but pressure low → not runaway
    cgroups = [
        _row("/", wbytes=10 * 1024**3),
        _row("/user.slice", wbytes=9 * 1024**3),
        _row("/system.slice", wbytes=1 * 1024**3),
    ]
    v = mod.classify(
        True, _pressure(0.0, 0.0), cgroups, True)
    # Falls through to imbalanced_readers? Only if read
    # dominance is also high. Otherwise ok_balanced.
    assert v["verdict"] == "ok_balanced"


def test_classify_io_throttled_long():
    cgroups = [_row("/", wbytes=100_000_000)]
    v = mod.classify(
        True, _pressure(0.0, 20.0), cgroups, True)
    assert v["verdict"] == "io_throttled_long"


def test_classify_imbalanced_readers():
    cgroups = [
        _row("/", rbytes=10 * 1024**3),
        _row("/user.slice", rbytes=9 * 1024**3),
        _row("/system.slice", rbytes=1 * 1024**3),
    ]
    v = mod.classify(
        True, _pressure(0.0, 0.0), cgroups, True)
    assert v["verdict"] == "imbalanced_readers"
    assert v["top_reader"] == "/user.slice"


# Priority : runaway > throttled > imbalanced
def test_priority_runaway_over_throttled():
    cgroups = [
        _row("/", wbytes=10 * 1024**3),
        _row("/user.slice", wbytes=9 * 1024**3),
    ]
    v = mod.classify(
        True, _pressure(50.0, 30.0), cgroups, True)
    assert v["verdict"] == "runaway_writer"


# --- status integration ----------------------------------------

def test_status_unknown(tmp_path):
    out = mod.status(None, str(tmp_path / "nope"))
    assert out["ok"] is False
    assert out["verdict"]["verdict"] == "unknown"


def test_status_no_cgroup_v2(tmp_path):
    # No cgroup.controllers file at all
    (tmp_path / "subdir").mkdir()
    out = mod.status(None, str(tmp_path))
    assert out["verdict"]["verdict"] == "no_cgroup_v2"


def test_status_ok_balanced(tmp_path):
    _mk_v2_root(tmp_path, with_io=True)
    _mk_cgroup(tmp_path, "/", wbytes=1_000_000_000,
                pressure_full_avg10=0.0,
                pressure_full_avg300=0.0)
    _mk_cgroup(tmp_path, "system.slice", wbytes=500_000_000)
    _mk_cgroup(tmp_path, "user.slice", wbytes=500_000_000)
    out = mod.status(None, str(tmp_path))
    assert out["ok"] is True
    assert out["verdict"]["verdict"] == "ok_balanced"
    # top_writers excludes the root row
    assert all(w["path"] != "/" for w in out["top_writers"])
