"""Tests for modules/btf_bpf_audit.py — R&D #66.2."""
from __future__ import annotations

import os

import pytest

from gpu_dashboard.modules import btf_bpf_audit as mod


# --- list_btf_entries -------------------------------------------

def test_list_btf_entries_missing(tmp_path):
    assert mod.list_btf_entries(str(tmp_path / "nope")) == []


def test_list_btf_entries_skips_vmlinux(tmp_path):
    (tmp_path / "vmlinux").write_bytes(b"x" * 200_000)
    (tmp_path / "ahci").write_bytes(b"x" * 1000)
    (tmp_path / "nvme").write_bytes(b"x" * 1000)
    out = mod.list_btf_entries(str(tmp_path))
    assert out == ["ahci", "nvme"]


# --- list_loaded_modules ---------------------------------------

def _mk_module(root, name, *, refcnt=True):
    d = root / name
    d.mkdir(parents=True, exist_ok=True)
    if refcnt:
        (d / "refcnt").write_text("0\n")


def test_list_loaded_modules_missing(tmp_path):
    assert mod.list_loaded_modules(str(tmp_path / "nope")) == []


def test_list_loaded_modules_excludes_builtins(tmp_path):
    _mk_module(tmp_path, "ahci", refcnt=True)
    _mk_module(tmp_path, "nvme", refcnt=True)
    _mk_module(tmp_path, "printk", refcnt=False)
    out = mod.list_loaded_modules(str(tmp_path))
    assert out == ["ahci", "nvme"]


# --- vmlinux_btf_size ------------------------------------------

def test_vmlinux_size_missing(tmp_path):
    assert mod.vmlinux_btf_size(str(tmp_path)) is None


def test_vmlinux_size_present(tmp_path):
    (tmp_path / "vmlinux").write_bytes(b"x" * 12345)
    assert mod.vmlinux_btf_size(str(tmp_path)) == 12345


# --- bpf_pinfs_present -----------------------------------------

def test_bpf_pinfs_missing(tmp_path):
    out = mod.bpf_pinfs_present(str(tmp_path / "nope"))
    assert out == {"present": False, "readable": False, "entries": 0}


def test_bpf_pinfs_present_empty(tmp_path):
    out = mod.bpf_pinfs_present(str(tmp_path))
    assert out["present"] is True
    assert out["readable"] is True
    assert out["entries"] == 0


# --- classify ---------------------------------------------------

def test_classify_unknown():
    v = mod.classify(None, [], [], False)
    assert v["verdict"] == "unknown"


def test_classify_vmlinux_missing():
    v = mod.classify(None, [], ["ahci", "nvme"], True)
    assert v["verdict"] == "vmlinux_btf_missing"


def test_classify_vmlinux_zero_byte():
    v = mod.classify(0, [], ["ahci", "nvme"], True)
    assert v["verdict"] == "vmlinux_btf_missing"


def test_classify_stale_btf_tiny_blob():
    v = mod.classify(50_000,
                          ["ahci", "nvme", "x", "y"],
                          ["ahci", "nvme", "x", "y"],
                          True)
    assert v["verdict"] == "stale_btf"


def test_classify_module_btf_missing():
    # Healthy vmlinux blob but only 1 of 10 modules has BTF.
    v = mod.classify(3_000_000,
                          ["ahci"],
                          [f"m{i}" for i in range(10)],
                          True)
    assert v["verdict"] == "module_btf_missing"


def test_classify_module_count_floor_skips_coverage_check():
    # Only 3 modules loaded → don't even bother computing
    # coverage (tiny systems / containers).
    v = mod.classify(3_000_000,
                          [],
                          ["m1", "m2", "m3"],
                          True)
    assert v["verdict"] == "ok"


def test_classify_ok():
    btfs = [f"m{i}" for i in range(80)]
    mods = [f"m{i}" for i in range(100)]
    v = mod.classify(6_000_000, btfs, mods, True)
    assert v["verdict"] == "ok"


# Priority : vmlinux missing > stale > module coverage.
def test_priority_vmlinux_over_stale():
    v = mod.classify(None, [], ["a", "b", "c", "d"], True)
    assert v["verdict"] == "vmlinux_btf_missing"


def test_priority_stale_over_module():
    v = mod.classify(50_000, [],
                          [f"m{i}" for i in range(20)], True)
    assert v["verdict"] == "stale_btf"


# --- status integration -----------------------------------------

def test_status_unknown(tmp_path):
    out = mod.status(None,
                          str(tmp_path / "nobtf"),
                          str(tmp_path / "nomod"),
                          str(tmp_path / "nobpf"))
    assert out["ok"] is False
    assert out["verdict"]["verdict"] == "unknown"


def test_status_live_like(tmp_path):
    btf = tmp_path / "btf"; btf.mkdir()
    (btf / "vmlinux").write_bytes(b"x" * 6_000_000)
    for n in ("ahci", "nvme", "i915"):
        (btf / n).write_bytes(b"x" * 1000)
    mod_dir = tmp_path / "mods"; mod_dir.mkdir()
    for n in ("ahci", "nvme", "i915", "ext4", "btrfs"):
        _mk_module(mod_dir, n, refcnt=True)
    bpf_dir = tmp_path / "bpf"; bpf_dir.mkdir()
    out = mod.status(None, str(btf), str(mod_dir), str(bpf_dir))
    assert out["ok"] is True
    assert out["vmlinux_btf_bytes"] == 6_000_000
    assert out["module_btf_count"] == 3
    assert out["loaded_module_count"] == 5
    assert out["verdict"]["verdict"] == "ok"
