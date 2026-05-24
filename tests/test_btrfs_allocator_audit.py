"""Tests for modules/btrfs_allocator_audit.py — R&D #80.3."""
from __future__ import annotations

import os

import pytest

from gpu_dashboard.modules import btrfs_allocator_audit as mod


def _mk_fs(root, uuid, profiles):
    """profiles = {(type, profile): {key: val}}"""
    base = root / uuid / "allocation"
    base.mkdir(parents=True, exist_ok=True)
    for (t, p), vals in profiles.items():
        pdir = base / t / p
        pdir.mkdir(parents=True, exist_ok=True)
        for k, v in vals.items():
            (pdir / k).write_text(f"{v}\n")


def _mk_features(root):
    """Some kernels have a `features` pseudo-dir alongside FSes."""
    (root / "features").mkdir(parents=True, exist_ok=True)


# --- list_filesystems ------------------------------------------

def test_list_missing(tmp_path):
    assert mod.list_filesystems(str(tmp_path / "nope")) == []


def test_list_only_features(tmp_path):
    _mk_features(tmp_path)
    assert mod.list_filesystems(str(tmp_path)) == []


def test_list_one_fs(tmp_path):
    _mk_features(tmp_path)
    _mk_fs(tmp_path, "abc-def",
              {("data", "single"): {"bytes_used": 100}})
    assert mod.list_filesystems(str(tmp_path)) == ["abc-def"]


# --- read_fs ---------------------------------------------------

def test_read_fs(tmp_path):
    _mk_fs(tmp_path, "uuid1", {
        ("data", "single"): {
            "bytes_used": 1000, "disk_total": 2000,
            "total_bytes": 1500, "disk_used": 1000},
        ("metadata", "dup"): {
            "bytes_used": 50, "total_bytes": 100,
            "disk_total": 200, "disk_used": 100},
    })
    out = mod.read_fs(str(tmp_path), "uuid1")
    assert out["data"]["single"]["bytes_used"] == 1000
    assert out["metadata"]["dup"]["total_bytes"] == 100


# --- classify --------------------------------------------------

def test_classify_unknown():
    v = mod.classify(False, [])
    assert v["verdict"] == "unknown"


def test_classify_na():
    v = mod.classify(True, [])
    assert v["verdict"] == "n/a"


def _ok_fs(uuid="uuid1"):
    return {
        "uuid": uuid,
        "allocation": {
            "data": {"single": {
                "bytes_used": 1_000_000_000,
                "disk_total": 1_100_000_000,
                "total_bytes": 1_100_000_000,
                "disk_used": 1_000_000_000}},
            "metadata": {"dup": {
                "bytes_used": 100_000_000,
                "disk_total": 500_000_000,
                "total_bytes": 500_000_000,
                "disk_used": 100_000_000}},
            "system": {"dup": {
                "bytes_used": 16384,
                "disk_total": 33554432,
                "total_bytes": 33554432,
                "disk_used": 16384}},
        }}


def test_classify_ok():
    v = mod.classify(True, [_ok_fs()])
    assert v["verdict"] == "ok"


def test_classify_metadata_full():
    fs = _ok_fs()
    fs["allocation"]["metadata"]["dup"]["bytes_used"] = 480_000_000
    fs["allocation"]["metadata"]["dup"]["total_bytes"] = 500_000_000
    v = mod.classify(True, [fs])
    assert v["verdict"] == "metadata_full_imminent"
    assert v["profile"] == "dup"


def test_classify_metadata_full_below_floor_ok():
    # total_bytes < 100 MiB → don't fire
    fs = _ok_fs()
    fs["allocation"]["metadata"]["dup"] = {
        "bytes_used": 50_000_000, "total_bytes": 50_000_000,
        "disk_total": 50_000_000, "disk_used": 50_000_000}
    v = mod.classify(True, [fs])
    assert v["verdict"] == "ok"


def test_classify_unbalanced_chunks():
    fs = _ok_fs()
    # 10 GiB allocated but only 1 GiB used
    fs["allocation"]["data"]["single"] = {
        "bytes_used": 1_000_000_000,
        "disk_total": 11 * 1024**3,
        "total_bytes": 11 * 1024**3,
        "disk_used": 1_000_000_000}
    v = mod.classify(True, [fs])
    assert v["verdict"] == "unbalanced_chunks"


def test_classify_mixed_profile():
    fs = _ok_fs()
    fs["allocation"]["data"]["raid1"] = {
        "bytes_used": 1000, "disk_total": 2000,
        "total_bytes": 2000, "disk_used": 1000}
    # Now data has both single + raid1
    v = mod.classify(True, [fs])
    assert v["verdict"] == "mixed_profile_unexpected"


# Priority : metadata_full > unbalanced > mixed
def test_priority_metadata_over_unbalanced():
    fs = _ok_fs()
    fs["allocation"]["metadata"]["dup"]["bytes_used"] = 480_000_000
    fs["allocation"]["metadata"]["dup"]["total_bytes"] = 500_000_000
    fs["allocation"]["data"]["single"] = {
        "bytes_used": 1_000_000_000,
        "disk_total": 11 * 1024**3,
        "total_bytes": 11 * 1024**3,
        "disk_used": 1_000_000_000}
    v = mod.classify(True, [fs])
    assert v["verdict"] == "metadata_full_imminent"


# --- status integration ----------------------------------------

def test_status_unknown(tmp_path):
    out = mod.status(None, str(tmp_path / "nope"))
    assert out["ok"] is False
    assert out["verdict"]["verdict"] == "unknown"


def test_status_na(tmp_path):
    _mk_features(tmp_path)
    out = mod.status(None, str(tmp_path))
    assert out["ok"] is True  # n/a is acceptable
    assert out["verdict"]["verdict"] == "n/a"


def test_status_ok_synthetic(tmp_path):
    _mk_features(tmp_path)
    _mk_fs(tmp_path, "uuid1", {
        ("data", "single"): {
            "bytes_used": 1_000_000_000,
            "disk_total": 1_100_000_000,
            "total_bytes": 1_100_000_000,
            "disk_used": 1_000_000_000},
        ("metadata", "dup"): {
            "bytes_used": 100_000_000,
            "disk_total": 500_000_000,
            "total_bytes": 500_000_000,
            "disk_used": 100_000_000},
    })
    out = mod.status(None, str(tmp_path))
    assert out["ok"] is True
    assert out["fs_count"] == 1
    assert out["verdict"]["verdict"] == "ok"


def test_status_metadata_full(tmp_path):
    _mk_features(tmp_path)
    _mk_fs(tmp_path, "uuid1", {
        ("metadata", "dup"): {
            "bytes_used": 480_000_000,
            "disk_total": 500_000_000,
            "total_bytes": 500_000_000,
            "disk_used": 480_000_000},
    })
    out = mod.status(None, str(tmp_path))
    assert out["ok"] is False
    assert out["verdict"]["verdict"] == "metadata_full_imminent"
