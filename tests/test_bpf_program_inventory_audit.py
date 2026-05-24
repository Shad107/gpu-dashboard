"""Tests for modules/bpf_program_inventory_audit.py — R&D #81.2."""
from __future__ import annotations

import os

import pytest

from gpu_dashboard.modules import bpf_program_inventory_audit as mod


def _mk_mounts(tmp_path, bpf_path="/sys/fs/bpf"):
    p = tmp_path / "mounts"
    p.write_text(
        "tmpfs /run tmpfs rw,nosuid 0 0\n"
        f"bpf {bpf_path} bpf rw,nosuid,nodev,noexec 0 0\n"
        "ext4 / ext4 rw 0 0\n")
    return str(p)


def _mk_unmounted_mounts(tmp_path):
    p = tmp_path / "mounts"
    p.write_text("tmpfs /run tmpfs rw 0 0\n")
    return str(p)


def _mk_fdinfo(tmp_path, pid, fd_to_content):
    """Create /proc/<pid>/fdinfo/<fd> files."""
    d = tmp_path / str(pid) / "fdinfo"
    d.mkdir(parents=True, exist_ok=True)
    for fd, content in fd_to_content.items():
        (d / str(fd)).write_text(content)


# --- is_bpffs_mounted ------------------------------------------

def test_is_bpffs_mounted_true(tmp_path):
    m = _mk_mounts(tmp_path)
    assert mod.is_bpffs_mounted(m, "/sys/fs/bpf") is True


def test_is_bpffs_mounted_false(tmp_path):
    m = _mk_unmounted_mounts(tmp_path)
    assert mod.is_bpffs_mounted(m, "/sys/fs/bpf") is False


def test_is_bpffs_mounted_missing(tmp_path):
    assert mod.is_bpffs_mounted(
        str(tmp_path / "nope"), "/sys/fs/bpf") is False


# --- list_pins -------------------------------------------------

def test_list_pins_missing(tmp_path):
    count, readable = mod.list_pins(str(tmp_path / "nope"))
    assert count is None
    assert readable is False


def test_list_pins_empty(tmp_path):
    bpf = tmp_path / "bpf"
    bpf.mkdir()
    count, readable = mod.list_pins(str(bpf))
    assert count == 0
    assert readable is True


def test_list_pins_populated(tmp_path):
    bpf = tmp_path / "bpf"
    bpf.mkdir()
    (bpf / "prog1").write_text("")
    (bpf / "prog2").write_text("")
    (bpf / "cilium").mkdir()
    (bpf / "cilium" / "policy_map").write_text("")
    count, readable = mod.list_pins(str(bpf))
    assert count == 4
    assert readable is True


# --- scan_user_fdinfo ------------------------------------------

def test_scan_fdinfo_empty(tmp_path):
    progs, maps, pids = mod.scan_user_fdinfo(str(tmp_path))
    assert progs == set()
    assert maps == set()


def test_scan_fdinfo_with_bpf_refs(tmp_path):
    _mk_fdinfo(tmp_path, 100, {
        "10": "pos:	0\nflags:	02\nmnt_id:	15\nprog_id:	42\n",
        "11": "pos:	0\nflags:	02\nmnt_id:	15\nmap_id:	7\n",
    })
    _mk_fdinfo(tmp_path, 200, {
        "5": "pos:	0\nprog_id:	42\n",
        "6": "pos:	0\nmap_id:	9\n",
    })
    progs, maps, pids = mod.scan_user_fdinfo(str(tmp_path))
    assert progs == {42}  # de-duplicated across pids
    assert maps == {7, 9}
    assert pids == 2


def test_scan_fdinfo_skips_non_bpf(tmp_path):
    _mk_fdinfo(tmp_path, 100, {
        "10": "pos:	0\nflags:	02\n",  # regular file fd
    })
    progs, maps, pids = mod.scan_user_fdinfo(str(tmp_path))
    assert progs == set()
    assert maps == set()
    assert pids == 1


# --- classify --------------------------------------------------

def test_classify_unknown():
    v = mod.classify(False, None, False, set(), set(), 0)
    assert v["verdict"] == "unknown"


def test_classify_excessive_pins():
    v = mod.classify(True, 100, True, set(), set(), 10)
    assert v["verdict"] == "excessive_pins"
    assert v["pin_count"] == 100


def test_classify_many_user_refs():
    progs = set(range(1, 31))   # 30 progs
    maps = set(range(40, 70))   # 30 maps -> 60 total
    v = mod.classify(True, 0, True, progs, maps, 10)
    assert v["verdict"] == "many_user_prog_refs"


def test_classify_pins_present():
    v = mod.classify(True, 5, True, {1, 2}, {3}, 10)
    assert v["verdict"] == "pins_present"


def test_classify_requires_root():
    # mounted, unreadable, no user refs
    v = mod.classify(True, None, False, set(), set(), 100)
    assert v["verdict"] == "requires_root"


def test_classify_ok_empty():
    # mounted, readable, zero pins, zero refs
    v = mod.classify(True, 0, True, set(), set(), 10)
    assert v["verdict"] == "ok_empty"


# Priority : excessive_pins > many_refs > pins_present
def test_priority_excessive_pins_over_refs():
    v = mod.classify(True, 100, True,
                       set(range(60)), set(), 10)
    assert v["verdict"] == "excessive_pins"


# --- status integration ----------------------------------------

def test_status_unknown(tmp_path):
    out = mod.status(None, str(tmp_path / "bpf"),
                       str(tmp_path / "nope_mounts"),
                       str(tmp_path / "nope_proc"))
    assert out["ok"] is False
    assert out["verdict"]["verdict"] == "unknown"


def test_status_ok_empty(tmp_path):
    bpf = tmp_path / "bpf"
    bpf.mkdir()
    mounts = _mk_mounts(tmp_path, bpf_path=str(bpf))
    proc = tmp_path / "proc"
    proc.mkdir()
    out = mod.status(None, str(bpf), mounts, str(proc))
    assert out["ok"] is True
    assert out["bpffs_mounted"] is True
    assert out["pin_count"] == 0
    assert out["verdict"]["verdict"] == "ok_empty"


def test_status_excessive_pins(tmp_path):
    bpf = tmp_path / "bpf"
    bpf.mkdir()
    for i in range(60):
        (bpf / f"prog_{i}").write_text("")
    mounts = _mk_mounts(tmp_path, bpf_path=str(bpf))
    proc = tmp_path / "proc"
    proc.mkdir()
    out = mod.status(None, str(bpf), mounts, str(proc))
    assert out["ok"] is False
    assert out["verdict"]["verdict"] == "excessive_pins"
