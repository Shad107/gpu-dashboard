"""Tests for modules/loop_device_audit.py — R&D #84.2."""
from __future__ import annotations

import pytest

from gpu_dashboard.modules import loop_device_audit as mod


def _mk_loop(tmp_path, name, *, backing="", size=0, ro=0):
    d = tmp_path / name
    d.mkdir(parents=True, exist_ok=True)
    (d / "size").write_text(f"{size}\n")
    (d / "ro").write_text(f"{ro}\n")
    loop_dir = d / "loop"
    loop_dir.mkdir(exist_ok=True)
    if backing:
        (loop_dir / "backing_file").write_text(backing + "\n")
    else:
        # active loop with empty backing_file (rare) ; create
        # the file to test "inactive" semantics
        (loop_dir / "backing_file").write_text("")


def _mk_module(tmp_path):
    d = tmp_path / "loop_mod"
    d.mkdir(parents=True, exist_ok=True)
    return str(d)


# --- list_loops ------------------------------------------------

def test_list_loops_missing(tmp_path):
    assert mod.list_loops(str(tmp_path / "nope")) == []


def test_list_loops(tmp_path):
    _mk_loop(tmp_path, "loop0", backing="/x")
    _mk_loop(tmp_path, "loop10", backing="/y")
    _mk_loop(tmp_path, "sda")  # not a loop
    out = mod.list_loops(str(tmp_path))
    assert "loop0" in out
    assert "loop10" in out
    assert "sda" not in out


# --- read_loop -------------------------------------------------

def test_read_loop_active(tmp_path):
    _mk_loop(tmp_path, "loop0",
              backing="/var/lib/snap/x.snap",
              size=12345)
    out = mod.read_loop(str(tmp_path), "loop0")
    assert out["backing_file"] == "/var/lib/snap/x.snap"
    assert out["size_sectors"] == 12345


# --- _is_deleted -----------------------------------------------

def test_is_deleted_true():
    assert mod._is_deleted("/tmp/x (deleted)") is True


def test_is_deleted_false():
    assert mod._is_deleted("/tmp/x") is False


def test_is_deleted_none():
    assert mod._is_deleted(None) is False


def test_is_deleted_empty():
    assert mod._is_deleted("") is False


# --- _is_unstable ----------------------------------------------

def test_is_unstable_tmp():
    assert mod._is_unstable("/tmp/foo.img") is True


def test_is_unstable_shm():
    assert mod._is_unstable("/dev/shm/x") is True


def test_is_unstable_snap_ok():
    assert mod._is_unstable(
        "/var/lib/snapd/snaps/core.snap") is False


def test_is_unstable_with_deleted_suffix():
    assert mod._is_unstable(
        "/tmp/x (deleted)") is True


def test_is_unstable_none():
    assert mod._is_unstable(None) is False


# --- classify --------------------------------------------------

def test_classify_na():
    v = mod.classify([], module_loaded=False)
    assert v["verdict"] == "n/a"


def test_classify_ok_empty():
    v = mod.classify([], module_loaded=True)
    assert v["verdict"] == "ok"


def _loop(name, backing, size=12345):
    return {"name": name, "backing_file": backing,
              "size_sectors": size, "ro": 0}


def test_classify_ok_few_active():
    v = mod.classify([
        _loop("loop0", "/var/lib/snap/a.snap"),
        _loop("loop1", "/var/lib/snap/b.snap"),
    ], module_loaded=True)
    assert v["verdict"] == "ok"


def test_classify_excessive():
    loops = [_loop(f"loop{i}",
                       f"/var/lib/snap/{i}.snap")
              for i in range(10)]
    v = mod.classify(loops, module_loaded=True)
    assert v["verdict"] == "excessive_loops"


def test_classify_unstable():
    v = mod.classify([
        _loop("loop0", "/tmp/leak.img"),
    ], module_loaded=True)
    assert v["verdict"] == "loop_unstable_backing"


def test_classify_deleted():
    v = mod.classify([
        _loop("loop0", "/var/lib/snap/x.snap (deleted)"),
    ], module_loaded=True)
    assert v["verdict"] == "loop_deleted_backing"


# Priority : deleted > unstable > excessive
def test_priority_deleted_over_unstable():
    v = mod.classify([
        _loop("loop0", "/var/lib/snap/x.snap (deleted)"),
        _loop("loop1", "/tmp/leak.img"),
    ], module_loaded=True)
    assert v["verdict"] == "loop_deleted_backing"


def test_priority_unstable_over_excessive():
    loops = ([_loop(f"loop{i}",
                        f"/var/lib/snap/{i}.snap")
                for i in range(10)]
              + [_loop("loop20", "/tmp/x.img")])
    v = mod.classify(loops, module_loaded=True)
    assert v["verdict"] == "loop_unstable_backing"


# --- status integration ----------------------------------------

def test_status_na(tmp_path):
    out = mod.status(None,
                       str(tmp_path / "no_block"),
                       str(tmp_path / "no_mod"))
    assert out["verdict"]["verdict"] == "n/a"


def test_status_ok_synthetic(tmp_path):
    block = tmp_path / "block"
    block.mkdir()
    _mk_loop(block, "loop0",
              backing="/var/lib/snap/core.snap",
              size=12345)
    mod_dir = _mk_module(tmp_path)
    out = mod.status(None, str(block), mod_dir)
    assert out["loop_count_active"] == 1
    assert out["verdict"]["verdict"] == "ok"


def test_status_deleted_backing_synthetic(tmp_path):
    block = tmp_path / "block"
    block.mkdir()
    _mk_loop(block, "loop0",
              backing="/var/snap/x.snap (deleted)",
              size=100)
    mod_dir = _mk_module(tmp_path)
    out = mod.status(None, str(block), mod_dir)
    assert out["ok"] is False
    assert (out["verdict"]["verdict"]
            == "loop_deleted_backing")
