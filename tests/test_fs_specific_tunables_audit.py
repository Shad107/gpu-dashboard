"""Tests for modules/fs_specific_tunables_audit.py — R&D #68.3."""
from __future__ import annotations

import os

import pytest

from gpu_dashboard.modules import fs_specific_tunables_audit as mod


def _mk_ext4(root, dev, *, errors_count=0, warning_count=0,
                  first_error_time=0,
                  lifetime_write_kbytes=12345):
    d = root / dev
    d.mkdir(parents=True, exist_ok=True)
    (d / "errors_count").write_text(f"{errors_count}\n")
    (d / "warning_count").write_text(f"{warning_count}\n")
    (d / "first_error_time").write_text(f"{first_error_time}\n")
    (d / "lifetime_write_kbytes").write_text(
        f"{lifetime_write_kbytes}\n")


def _mk_xfs(root, dev, *, stats_text=""):
    d = root / dev / "stats"
    d.mkdir(parents=True, exist_ok=True)
    (d / "stats").write_text(stats_text)


def _mk_f2fs(root, dev, *, features="encryption", gc_idle=1):
    d = root / dev
    d.mkdir(parents=True, exist_ok=True)
    (d / "features").write_text(features + "\n")
    (d / "gc_idle").write_text(f"{gc_idle}\n")


# --- list_fs_devices --------------------------------------------

def test_list_fs_devices_missing(tmp_path):
    assert mod.list_fs_devices(str(tmp_path / "nope")) == []


def test_list_fs_devices_skips_features(tmp_path):
    (tmp_path / "features").mkdir()
    _mk_ext4(tmp_path, "sda2")
    out = mod.list_fs_devices(str(tmp_path))
    assert out == ["sda2"]


# --- scan_ext4 / scan_xfs / scan_f2fs ---------------------------

def test_scan_ext4_clean(tmp_path):
    _mk_ext4(tmp_path, "sda2", lifetime_write_kbytes=999999)
    out = mod.scan_ext4(str(tmp_path))
    assert len(out) == 1
    assert out[0]["dev"] == "sda2"
    assert out[0]["errors_count"] == 0
    assert out[0]["lifetime_write_kbytes"] == 999999


def test_scan_ext4_with_errors(tmp_path):
    _mk_ext4(tmp_path, "sda2", errors_count=3,
                first_error_time=1700000000)
    out = mod.scan_ext4(str(tmp_path))
    assert out[0]["errors_count"] == 3
    assert out[0]["first_error_time"] == 1700000000


def test_scan_xfs_no_corruption(tmp_path):
    _mk_xfs(tmp_path, "sdb1",
                 stats_text="bs_chk 0\nfcntr_corruption 0\n")
    out = mod.scan_xfs(str(tmp_path))
    assert out[0]["metadata_corruption_counter"] == 0


def test_scan_xfs_with_corruption(tmp_path):
    _mk_xfs(tmp_path, "sdb1",
                 stats_text="bs_chk 5\nfcntr_corruption 2\n"
                              "ag_unhealth 1\n")
    out = mod.scan_xfs(str(tmp_path))
    assert out[0]["metadata_corruption_counter"] == 8


def test_scan_f2fs_gc_on(tmp_path):
    _mk_f2fs(tmp_path, "sdc1", gc_idle=1)
    out = mod.scan_f2fs(str(tmp_path))
    assert out[0]["gc_idle"] == 1


# --- classify ---------------------------------------------------

def test_classify_unknown_no_surfaces():
    v = mod.classify([], [], [], False, False, False)
    assert v["verdict"] == "unknown"


def test_classify_ok():
    v = mod.classify(
        [{"dev": "sda2", "errors_count": 0,
            "warning_count": 0, "first_error_time": 0,
            "lifetime_write_kbytes": 12345}],
        [], [], True, False, False)
    assert v["verdict"] == "ok"


def test_classify_ext4_errors():
    v = mod.classify(
        [{"dev": "sda2", "errors_count": 5,
            "warning_count": 0, "first_error_time": 1700000000,
            "lifetime_write_kbytes": 12345}],
        [], [], True, False, False)
    assert v["verdict"] == "ext4_errors_logged"
    assert "sda2" in v["reason"]


def test_classify_ext4_first_error_only():
    # errors_count cleared but first_error_time still set →
    # historical issue worth surfacing.
    v = mod.classify(
        [{"dev": "sda2", "errors_count": 0,
            "warning_count": 0, "first_error_time": 1700000000,
            "lifetime_write_kbytes": 12345}],
        [], [], True, False, False)
    assert v["verdict"] == "ext4_errors_logged"


def test_classify_xfs_corruption():
    v = mod.classify(
        [], [{"dev": "sdb1", "stats_present": True,
                "metadata_corruption_counter": 3}],
        [], False, True, False)
    assert v["verdict"] == "xfs_metadata_corruption_counter"


def test_classify_f2fs_gc_disabled():
    v = mod.classify(
        [], [], [{"dev": "sdc1", "features": "x",
                    "gc_idle": 0}],
        False, False, True)
    assert v["verdict"] == "f2fs_gc_disabled"


def test_classify_requires_root():
    v = mod.classify(
        [{"dev": "sda2", "errors_count": None,
            "warning_count": None, "first_error_time": None,
            "lifetime_write_kbytes": None}],
        [], [], True, False, False)
    assert v["verdict"] == "requires_root"


# Priority : ext4 errors > xfs corruption > f2fs gc.
def test_priority_ext4_over_xfs():
    v = mod.classify(
        [{"dev": "sda2", "errors_count": 1,
            "warning_count": 0, "first_error_time": 1700,
            "lifetime_write_kbytes": 10}],
        [{"dev": "sdb1", "stats_present": True,
            "metadata_corruption_counter": 5}],
        [], True, True, False)
    assert v["verdict"] == "ext4_errors_logged"


def test_priority_xfs_over_f2fs():
    v = mod.classify(
        [], [{"dev": "sdb1", "stats_present": True,
                "metadata_corruption_counter": 5}],
        [{"dev": "sdc1", "features": "x", "gc_idle": 0}],
        False, True, True)
    assert v["verdict"] == "xfs_metadata_corruption_counter"


# --- status integration -----------------------------------------

def test_status_unknown(tmp_path):
    out = mod.status(None,
                          str(tmp_path / "no_ext4"),
                          str(tmp_path / "no_xfs"),
                          str(tmp_path / "no_f2fs"))
    assert out["ok"] is False
    assert out["verdict"]["verdict"] == "unknown"


def test_status_ext4_only_ok(tmp_path):
    ext4 = tmp_path / "ext4"; ext4.mkdir()
    _mk_ext4(ext4, "sda2", lifetime_write_kbytes=21126050406)
    out = mod.status(None, str(ext4),
                          str(tmp_path / "no_xfs"),
                          str(tmp_path / "no_f2fs"))
    assert out["ok"] is True
    assert len(out["ext4_devices"]) == 1
    assert out["verdict"]["verdict"] == "ok"


def test_status_ext4_errors_synthetic(tmp_path):
    ext4 = tmp_path / "ext4"; ext4.mkdir()
    _mk_ext4(ext4, "sda2", errors_count=3,
                first_error_time=1700000000)
    out = mod.status(None, str(ext4),
                          str(tmp_path / "no_xfs"),
                          str(tmp_path / "no_f2fs"))
    assert out["verdict"]["verdict"] == "ext4_errors_logged"
