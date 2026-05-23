"""Tests for modules/block_queue_audit.py — R&D #43.2."""
from __future__ import annotations

from pathlib import Path

import pytest

from gpu_dashboard.modules import block_queue_audit as mod


def _mk_dev(sys_block: Path, dev: str, *,
              scheduler: str = "[none] mq-deadline",
              nr_requests: int = 256, read_ahead_kb: int = 128,
              rotational: int = 0, nomerges: int = 0,
              iostats: int = 1, rq_affinity: int = 1,
              max_sectors_kb: int = 4096, max_hw_sectors_kb: int = 32767,
              wbt_lat_usec: int = 0, write_cache: str = "write back",
              logical_block_size: int = 512,
              physical_block_size: int = 512,
              model: str | None = None):
    ddir = sys_block / dev
    qdir = ddir / "queue"
    qdir.mkdir(parents=True, exist_ok=True)
    (qdir / "scheduler").write_text(scheduler + "\n")
    (qdir / "nr_requests").write_text(str(nr_requests) + "\n")
    (qdir / "read_ahead_kb").write_text(str(read_ahead_kb) + "\n")
    (qdir / "rotational").write_text(str(rotational) + "\n")
    (qdir / "nomerges").write_text(str(nomerges) + "\n")
    (qdir / "iostats").write_text(str(iostats) + "\n")
    (qdir / "rq_affinity").write_text(str(rq_affinity) + "\n")
    (qdir / "max_sectors_kb").write_text(str(max_sectors_kb) + "\n")
    (qdir / "max_hw_sectors_kb").write_text(str(max_hw_sectors_kb) + "\n")
    (qdir / "wbt_lat_usec").write_text(str(wbt_lat_usec) + "\n")
    (qdir / "write_cache").write_text(write_cache + "\n")
    (qdir / "logical_block_size").write_text(
        str(logical_block_size) + "\n")
    (qdir / "physical_block_size").write_text(
        str(physical_block_size) + "\n")
    if model is not None:
        (ddir / "device").mkdir(exist_ok=True)
        (ddir / "device" / "model").write_text(model + "\n")


# --- parse_scheduler ----------------------------------------------

def test_parse_scheduler_active_first():
    a, av = mod.parse_scheduler("[none] mq-deadline")
    assert a == "none"
    assert "none" in av
    assert "mq-deadline" in av


def test_parse_scheduler_active_middle():
    a, av = mod.parse_scheduler("mq-deadline [bfq] kyber")
    assert a == "bfq"
    assert av == ["mq-deadline", "bfq", "kyber"]


def test_parse_scheduler_none_active():
    a, av = mod.parse_scheduler("none mq-deadline")
    assert a is None
    assert "mq-deadline" in av


def test_parse_scheduler_empty():
    assert mod.parse_scheduler(None) == (None, [])
    assert mod.parse_scheduler("") == (None, [])


# --- list_block_devices -------------------------------------------

def test_list_block_devices_skips_loop_ram(tmp_path):
    _mk_dev(tmp_path, "loop0")
    _mk_dev(tmp_path, "ram0")
    _mk_dev(tmp_path, "dm-0")
    _mk_dev(tmp_path, "nvme0n1")
    _mk_dev(tmp_path, "sda")
    assert mod.list_block_devices(str(tmp_path)) == ["nvme0n1", "sda"]


def test_list_block_devices_missing(tmp_path):
    assert mod.list_block_devices(str(tmp_path / "nope")) == []


# --- read_device --------------------------------------------------

def test_read_device_basic(tmp_path):
    _mk_dev(tmp_path, "nvme0n1", scheduler="[none]",
              rotational=0, read_ahead_kb=4096, model="Samsung 980 NVMe")
    d = mod.read_device(str(tmp_path), "nvme0n1")
    assert d["dev"] == "nvme0n1"
    assert d["scheduler"] == "none"
    assert d["rotational"] == 0
    assert d["read_ahead_kb"] == 4096
    assert d["model"] == "Samsung 980 NVMe"


# --- _looks_rotational --------------------------------------------

def test_looks_rotational_nvme_hint():
    assert mod._looks_rotational("Samsung 980 NVMe") is False


def test_looks_rotational_hdd_hint():
    assert mod._looks_rotational("WD Red Plus 6TB HDD") is True


def test_looks_rotational_unknown():
    assert mod._looks_rotational(None) is None
    assert mod._looks_rotational("UnknownModel123") is None


# --- classify ------------------------------------------------------

def _dev(name="sda", **overrides):
    base = {"dev": name, "scheduler": "none",
              "scheduler_available": ["none", "mq-deadline"],
              "nr_requests": 256, "read_ahead_kb": 4096,
              "rotational": 0, "wbt_lat_usec": 0, "model": None}
    base.update(overrides)
    return base


def test_classify_no_block_devices():
    v = mod.classify([])
    assert v["verdict"] == "no_block_devices"


def test_classify_ok():
    v = mod.classify([_dev()])
    assert v["verdict"] == "ok"


def test_classify_rotational_misdetect():
    # Model says NVMe but kernel says rotational=1.
    v = mod.classify([_dev(model="Samsung NVMe SSD", rotational=1)])
    assert v["verdict"] == "rotational_misdetect"
    assert "rotational" in v["recommendation"].lower()


def test_classify_scheduler_mismatch_bfq_on_ssd():
    v = mod.classify([_dev(scheduler="bfq", rotational=0,
                              read_ahead_kb=4096)])
    assert v["verdict"] == "scheduler_mismatch"
    assert "BFQ" in v["reason"]


def test_classify_scheduler_mismatch_none_on_hdd():
    v = mod.classify([_dev(scheduler="none", rotational=1,
                              read_ahead_kb=4096)])
    assert v["verdict"] == "scheduler_mismatch"


def test_classify_readahead_too_low():
    v = mod.classify([_dev(read_ahead_kb=128, rotational=0,
                              scheduler="none")])
    assert v["verdict"] == "readahead_too_low"
    assert "read_ahead_kb" in v["recommendation"]


def test_classify_wbt_throttling():
    v = mod.classify([_dev(wbt_lat_usec=2000, rotational=0,
                              read_ahead_kb=4096, scheduler="none")])
    assert v["verdict"] == "wbt_throttling"
    assert "wbt_lat_usec" in v["recommendation"]


def test_classify_priority_misdetect_wins():
    # Both rotational_misdetect + scheduler_mismatch true → first wins.
    v = mod.classify([_dev(model="Samsung NVMe SSD",
                              rotational=1, scheduler="none")])
    assert v["verdict"] == "rotational_misdetect"


def test_classify_priority_scheduler_over_readahead():
    v = mod.classify([_dev(scheduler="bfq", rotational=0,
                              read_ahead_kb=128)])
    assert v["verdict"] == "scheduler_mismatch"


def test_classify_priority_readahead_over_wbt():
    v = mod.classify([_dev(read_ahead_kb=128, rotational=0,
                              scheduler="none", wbt_lat_usec=2000)])
    assert v["verdict"] == "readahead_too_low"


def test_classify_worst_of_multiple_devices():
    a = _dev(name="sda", scheduler="none", rotational=0,
              read_ahead_kb=4096)
    b = _dev(name="sdb", scheduler="bfq", rotational=0,
              read_ahead_kb=4096)
    v = mod.classify([a, b])
    assert v["verdict"] == "scheduler_mismatch"
    assert "sdb" in v["reason"]


# --- status integration -------------------------------------------

def test_status_with_isolated_root(monkeypatch, tmp_path):
    sys_block = tmp_path / "block"
    sys_block.mkdir()
    _mk_dev(sys_block, "nvme0n1", scheduler="[none] mq-deadline",
              rotational=0, read_ahead_kb=128)
    monkeypatch.setattr(mod, "_SYS_BLOCK", str(sys_block))
    out = mod.status()
    assert out["ok"] is True
    assert out["device_count"] == 1
    assert out["verdict"]["verdict"] == "readahead_too_low"


def test_status_unknown(monkeypatch, tmp_path):
    monkeypatch.setattr(mod, "_SYS_BLOCK", str(tmp_path / "nope"))
    out = mod.status()
    assert out["ok"] is False
    assert out["verdict"]["verdict"] == "unknown"
