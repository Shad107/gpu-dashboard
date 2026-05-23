"""Tests for modules/bdi_writeback_audit.py — R&D #56.2."""
from __future__ import annotations

import pytest

from gpu_dashboard.modules import bdi_writeback_audit as mod


def _mk_bdi(root, maj_min, *, read_ahead_kb=128, max_ratio=100,
              min_ratio=0, stable_pages_required=0):
    d = root / maj_min
    d.mkdir(parents=True, exist_ok=True)
    (d / "read_ahead_kb").write_text(f"{read_ahead_kb}\n")
    (d / "max_ratio").write_text(f"{max_ratio}\n")
    (d / "min_ratio").write_text(f"{min_ratio}\n")
    (d / "stable_pages_required").write_text(
        f"{stable_pages_required}\n")
    return d


def _mk_block(root, name, *, dev, rotational=0):
    d = root / name
    d.mkdir(parents=True, exist_ok=True)
    (d / "dev").write_text(dev + "\n")
    q = d / "queue"
    q.mkdir(parents=True, exist_ok=True)
    (q / "rotational").write_text(f"{rotational}\n")
    return d


# --- list_bdis --------------------------------------------------

def test_list_bdis_missing(tmp_path):
    assert mod.list_bdis(str(tmp_path / "nope")) == []


def test_list_bdis(tmp_path):
    _mk_bdi(tmp_path, "8:0", read_ahead_kb=128, max_ratio=100)
    _mk_bdi(tmp_path, "259:0", read_ahead_kb=256, max_ratio=100)
    (tmp_path / "btrfs-control").mkdir()
    out = mod.list_bdis(str(tmp_path))
    assert len(out) == 2
    sda = next(b for b in out if b["id"] == "8:0")
    assert sda["read_ahead_kb"] == 128


# --- map_devices ------------------------------------------------

def test_map_devices(tmp_path):
    _mk_block(tmp_path, "sda", dev="8:0", rotational=0)
    _mk_block(tmp_path, "nvme0n1", dev="259:0", rotational=0)
    out = mod.map_devices(str(tmp_path))
    assert "8:0" in out
    assert out["259:0"]["name"] == "nvme0n1"
    assert out["259:0"]["is_nvme"] is True
    assert out["8:0"]["is_nvme"] is False


def test_map_devices_missing(tmp_path):
    assert mod.map_devices(str(tmp_path / "nope")) == {}


# --- parse_partitions -------------------------------------------

def test_parse_partitions():
    text = ("major minor  #blocks  name\n"
              "\n"
              "   8        0  100000  sda\n"
              "   8        1  10000   sda1\n"
              " 259        0  500000  nvme0n1\n")
    out = mod.parse_partitions(text)
    assert "8:0" in out
    assert "8:1" in out
    assert "259:0" in out


def test_parse_partitions_empty():
    assert mod.parse_partitions("") == []
    assert mod.parse_partitions(None) == []


# --- is_real_device ---------------------------------------------

def test_is_real_device():
    real = ["8:0", "8:1", "259:0"]
    dev_map = {"259:0": {"name": "nvme0n1"}}
    assert mod.is_real_device("8:0", real, dev_map) is True
    assert mod.is_real_device("259:0", real, dev_map) is True
    assert mod.is_real_device("0:62", real, dev_map) is False


# --- classify ---------------------------------------------------

def _bdi(id_="8:0", ra=128, max_r=100):
    return {"id": id_, "read_ahead_kb": ra, "max_ratio": max_r,
              "min_ratio": 0, "stable_pages_required": 0}


def _dev_map(**kwargs):
    return kwargs


def test_classify_unknown():
    v = mod.classify([], {}, [], 500, 3000)
    assert v["verdict"] == "unknown"


def test_classify_ok():
    bdis = [_bdi("8:0", ra=128, max_r=100)]
    devs = {"8:0": {"name": "sda", "rotational": 0,
                       "is_nvme": False}}
    v = mod.classify(bdis, devs, ["8:0"], 500, 3000)
    assert v["verdict"] == "ok"


def test_classify_stuck_max_ratio_pseudo_doesnt_fire():
    # 0:62 is a pseudo-BDI with max_ratio=1 — shouldn't fire
    # because it's not in partitions / device_map.
    bdis = [_bdi("0:62", ra=128, max_r=1),
              _bdi("8:0", ra=128, max_r=100)]
    devs = {"8:0": {"name": "sda", "rotational": 0,
                       "is_nvme": False}}
    v = mod.classify(bdis, devs, ["8:0"], 500, 3000)
    assert v["verdict"] == "ok"


def test_classify_stuck_real_storage():
    bdis = [_bdi("8:0", ra=128, max_r=1)]
    devs = {"8:0": {"name": "sda", "rotational": 0,
                       "is_nvme": False}}
    v = mod.classify(bdis, devs, ["8:0"], 500, 3000)
    assert v["verdict"] == "stuck_max_ratio_1"


def test_classify_readahead_nvme():
    bdis = [_bdi("259:0", ra=128, max_r=100)]
    devs = {"259:0": {"name": "nvme0n1", "rotational": 0,
                          "is_nvme": True}}
    v = mod.classify(bdis, devs, ["259:0"], 500, 3000)
    assert v["verdict"] == "readahead_below_128k_on_nvme"


def test_classify_readahead_nvme_high_ok():
    bdis = [_bdi("259:0", ra=1024, max_r=100)]
    devs = {"259:0": {"name": "nvme0n1", "rotational": 0,
                          "is_nvme": True}}
    v = mod.classify(bdis, devs, ["259:0"], 500, 3000)
    assert v["verdict"] == "ok"


def test_classify_writeback_above_3000():
    bdis = [_bdi("8:0")]
    devs = {"8:0": {"name": "sda", "rotational": 0,
                       "is_nvme": False}}
    v = mod.classify(bdis, devs, ["8:0"], 6000, 3000)
    assert v["verdict"] == "writeback_centisecs_above_3000"


def test_classify_priority_stuck_wins():
    bdis = [_bdi("259:0", ra=128, max_r=1)]
    devs = {"259:0": {"name": "nvme0n1", "rotational": 0,
                          "is_nvme": True}}
    v = mod.classify(bdis, devs, ["259:0"], 6000, 3000)
    assert v["verdict"] == "stuck_max_ratio_1"


# --- status integration -----------------------------------------

def test_status_unknown(tmp_path):
    out = mod.status(None, str(tmp_path / "nope"),
                       str(tmp_path / "noblock"),
                       str(tmp_path / "nopart"),
                       str(tmp_path / "novm"))
    assert out["ok"] is False
    assert out["verdict"]["verdict"] == "unknown"


def test_status_live_like(tmp_path):
    bdi = tmp_path / "bdi"
    _mk_bdi(bdi, "8:0", read_ahead_kb=128, max_ratio=100)
    _mk_bdi(bdi, "0:62", read_ahead_kb=128, max_ratio=1)
    block = tmp_path / "block"
    _mk_block(block, "sda", dev="8:0")
    part = tmp_path / "partitions"
    part.write_text("major minor  #blocks  name\n\n"
                       "   8        0  100  sda\n")
    vm = tmp_path / "vm"
    vm.mkdir()
    (vm / "dirty_writeback_centisecs").write_text("500\n")
    (vm / "dirty_expire_centisecs").write_text("3000\n")
    out = mod.status(None, str(bdi), str(block), str(part),
                       str(vm))
    assert out["ok"] is True
    assert out["bdi_count"] == 2
    # Real BDI is healthy → ok (pseudo 0:62 with max_ratio=1
    # filtered out).
    assert out["verdict"]["verdict"] == "ok"
