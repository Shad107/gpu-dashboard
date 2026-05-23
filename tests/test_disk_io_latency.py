"""Tests for modules/disk_io_latency.py — R&D #44.1."""
from __future__ import annotations

from pathlib import Path

import pytest

from gpu_dashboard.modules import disk_io_latency as mod


DISKSTATS_SAMPLE = """\
   7       0 loop0 14 0 34 0 0 0 0 0 0 1 0 0 0 0 0 0 0
   8       0 sda 1000000 100 8000000 50000 500000 50 4000000 25000 0 30000 75000 0 0 0 0 0 0
   8       1 sda1 100 5 800 50 50 5 400 25 0 30 75 0 0 0 0 0 0
 259       0 nvme0n1 2000000 200 16000000 200000 1000000 100 8000000 60000 4 40000 260000 0 0 0 0 0 0
"""


# --- _partition_stem ----------------------------------------------

def test_partition_stem_sda1():
    assert mod._partition_stem("sda1") == "sda"


def test_partition_stem_nvme():
    assert mod._partition_stem("nvme0n1p1") == "nvme0n1"


def test_partition_stem_root_device():
    assert mod._partition_stem("sda") is None
    assert mod._partition_stem("nvme0n1") is None


# --- parse_diskstats -----------------------------------------------

def test_parse_diskstats_filters_partitions():
    rows = mod.parse_diskstats(DISKSTATS_SAMPLE)
    devs = [r["dev"] for r in rows]
    # loop0 filtered (prefix skip), sda1 filtered (partition of sda)
    assert devs == ["sda", "nvme0n1"]


def test_parse_diskstats_counters():
    rows = mod.parse_diskstats(DISKSTATS_SAMPLE)
    sda = next(r for r in rows if r["dev"] == "sda")
    assert sda["reads_completed"] == 1000000
    assert sda["read_ticks"] == 50000
    assert sda["writes_completed"] == 500000


def test_parse_diskstats_empty():
    assert mod.parse_diskstats("") == []


def test_parse_diskstats_short_row_ignored():
    txt = "8 0 sda short\n"
    assert mod.parse_diskstats(txt) == []


# --- per_device_summary --------------------------------------------

def test_per_device_summary_avg_wait():
    row = {"reads_completed": 1000, "read_ticks": 5000,
             "writes_completed": 500, "write_ticks": 2500}
    s = mod.per_device_summary(row)
    assert s["avg_read_wait_ms"] == 5.0
    assert s["avg_write_wait_ms"] == 5.0


def test_per_device_summary_zero_ios():
    s = mod.per_device_summary({"reads_completed": 0,
                                   "writes_completed": 0,
                                   "read_ticks": 0, "write_ticks": 0})
    assert s["avg_read_wait_ms"] == 0.0
    assert s["avg_write_wait_ms"] == 0.0


# --- read_inflight + read_rotational ------------------------------

def test_read_inflight_basic(tmp_path):
    (tmp_path / "sda").mkdir()
    (tmp_path / "sda" / "inflight").write_text("  2  3\n")
    out = mod.read_inflight(str(tmp_path), "sda")
    assert out == {"read": 2, "write": 3}


def test_read_inflight_missing(tmp_path):
    out = mod.read_inflight(str(tmp_path), "sda")
    assert out == {"read": 0, "write": 0}


def test_read_rotational(tmp_path):
    p = tmp_path / "sda" / "queue"
    p.mkdir(parents=True)
    (p / "rotational").write_text("0\n")
    assert mod.read_rotational(str(tmp_path), "sda") == 0


# --- classify ------------------------------------------------------

def _dev(name="sda", rotational=0, reads=1_000_000, writes=500_000,
          avg_read_wait=2.0, avg_write_wait=2.0, inflight=0):
    return {"dev": name, "rotational": rotational,
              "reads_completed": reads,
              "writes_completed": writes,
              "avg_read_wait_ms": avg_read_wait,
              "avg_write_wait_ms": avg_write_wait,
              "inflight_read": 0, "inflight_write": inflight,
              "inflight_total": inflight,
              "read_ticks_ms": reads * avg_read_wait,
              "write_ticks_ms": writes * avg_write_wait,
              "ios_in_progress": 0}


def test_classify_no_devices():
    v = mod.classify([])
    assert v["verdict"] == "no_block_devices"


def test_classify_ok():
    v = mod.classify([_dev()])
    assert v["verdict"] == "ok"


def test_classify_queue_saturated_on_nvme():
    v = mod.classify([_dev(name="nvme0n1", rotational=0,
                              inflight=50)])
    assert v["verdict"] == "queue_saturated"


def test_classify_queue_sat_skipped_on_rotational():
    # HDDs naturally hold > 32 inflight ; don't flag.
    v = mod.classify([_dev(name="sda", rotational=1, inflight=50)])
    assert v["verdict"] == "ok"


def test_classify_read_stall():
    v = mod.classify([_dev(avg_read_wait=150.0)])
    assert v["verdict"] == "read_stall"
    assert "150" in v["reason"]


def test_classify_read_stall_skipped_below_floor():
    # Only 100 reads completed — below 1k floor.
    v = mod.classify([_dev(reads=100, avg_read_wait=500.0)])
    assert v["verdict"] == "ok"


def test_classify_write_stall():
    v = mod.classify([_dev(avg_write_wait=600.0)])
    assert v["verdict"] == "write_stall"


def test_classify_priority_saturation_wins_over_read_stall():
    v = mod.classify([_dev(rotational=0, inflight=50,
                              avg_read_wait=200.0)])
    assert v["verdict"] == "queue_saturated"


def test_classify_priority_read_over_write_stall():
    v = mod.classify([_dev(avg_read_wait=150.0,
                              avg_write_wait=600.0)])
    assert v["verdict"] == "read_stall"


# --- status integration -------------------------------------------

def test_status_with_isolated_files(monkeypatch, tmp_path):
    (tmp_path / "diskstats").write_text(DISKSTATS_SAMPLE)
    sys_block = tmp_path / "block"
    sys_block.mkdir()
    for n in ["sda", "nvme0n1"]:
        d = sys_block / n
        q = d / "queue"
        q.mkdir(parents=True)
        (d / "inflight").write_text("0 0\n")
        (q / "rotational").write_text("0\n")
    monkeypatch.setattr(mod, "_PROC_DISKSTATS",
                        str(tmp_path / "diskstats"))
    monkeypatch.setattr(mod, "_SYS_BLOCK", str(sys_block))
    out = mod.status()
    assert out["ok"] is True
    assert out["device_count"] == 2
    # The sample has fast latencies → verdict ok.
    assert out["verdict"]["verdict"] == "ok"


def test_status_unknown(monkeypatch, tmp_path):
    monkeypatch.setattr(mod, "_PROC_DISKSTATS",
                        str(tmp_path / "nope"))
    monkeypatch.setattr(mod, "_SYS_BLOCK",
                        str(tmp_path / "noblock"))
    out = mod.status()
    assert out["ok"] is False
    assert out["verdict"]["verdict"] == "unknown"
