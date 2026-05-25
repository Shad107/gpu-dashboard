"""Tests for modules/xfs_log_activity_audit.py R&D #110.2."""
from __future__ import annotations

import pytest

from gpu_dashboard.modules import xfs_log_activity_audit as mod


def test_parse_rw_row_present():
    text = (
        "extent_alloc 0 0 0 0\n"
        "rw 12345 67890\n"
        "attr 0 0 0 0\n")
    out = mod.parse_rw_row(text)
    assert out == {"reads": 12345, "writes": 67890}


def test_parse_rw_row_absent():
    assert mod.parse_rw_row("extent_alloc 0 0 0 0\n") is None
    assert mod.parse_rw_row(None) is None


def _mk_xfs(root, dev, stats_text=None):
    d = root / dev / "stats"
    d.mkdir(parents=True, exist_ok=True)
    if stats_text is not None:
        (d / "stats").write_text(stats_text)


def test_walk_xfs_empty(tmp_path):
    assert mod.walk_xfs(str(tmp_path / "nope")) == []


def test_walk_xfs_basic(tmp_path):
    _mk_xfs(tmp_path, "sda1", "rw 100 200\n")
    _mk_xfs(tmp_path, "sdb1", "extent_alloc 0\n")
    out = mod.walk_xfs(str(tmp_path))
    assert len(out) == 2
    by_dev = {f["dev"]: f for f in out}
    assert by_dev["sda1"]["rw"] == {"reads": 100,
                                      "writes": 200}
    assert by_dev["sdb1"]["rw"] is None


def test_classify_unknown():
    v = mod.classify(False, False, [])
    assert v["verdict"] == "unknown"


def test_classify_requires_root():
    v = mod.classify(True, False, [])
    assert v["verdict"] == "requires_root"


def test_classify_unknown_no_filesystems():
    v = mod.classify(True, True, [])
    assert v["verdict"] == "unknown"


def test_classify_ok():
    v = mod.classify(True, True, [
        {"dev": "sda1", "rw": {"reads": 100, "writes": 200}}])
    assert v["verdict"] == "ok"


def test_classify_stats_unreadable_accent():
    v = mod.classify(True, True, [
        {"dev": "sda1", "rw": None}])
    assert v["verdict"] == "xfs_stats_unreadable"


def test_status_unknown(tmp_path):
    out = mod.status(None, str(tmp_path / "nope"))
    assert out["verdict"]["verdict"] == "unknown"


def test_status_ok_synthetic(tmp_path):
    _mk_xfs(tmp_path, "sda1", "rw 100 200\n")
    out = mod.status(None, str(tmp_path))
    assert out["verdict"]["verdict"] == "ok"
    assert out["filesystem_count"] == 1
