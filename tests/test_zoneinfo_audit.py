"""Tests for modules/zoneinfo_audit.py — R&D #43.3."""
from __future__ import annotations

import pytest

from gpu_dashboard.modules import zoneinfo_audit as mod


ZONEINFO_SAMPLE = """\
Node 0, zone      DMA
  per-node stats
      nr_inactive_anon 1000
  pages free     2816
        min      8
        low      11
        high     14
        managed  3840
      nr_free_pages 2816
Node 0, zone     DMA32
  pages free     50000
        min      100
        low      200
        high     300
        managed  500000
Node 0, zone   Normal
  pages free     10
        min      8
        low      20
        high     30
        managed  1000000
"""


VMSTAT_SAMPLE = """\
nr_free_pages 60000
allocstall_normal 100
pgsteal_kswapd 500000
pgsteal_direct 5000
pgscan_kswapd 1000000
pgscan_direct 10000
compact_success 100
compact_fail 5
compact_stall 50
compact_daemon_wake 80
"""


# --- parse_zoneinfo ------------------------------------------------

def test_parse_zoneinfo_basic():
    zones = mod.parse_zoneinfo(ZONEINFO_SAMPLE)
    # Should have DMA, DMA32, Normal (per-node stats filtered).
    names = [z["zone"] for z in zones]
    assert names == ["DMA", "DMA32", "Normal"]
    dma = zones[0]
    assert dma["node"] == 0
    assert dma["free"] == 2816
    assert dma["low"] == 11
    assert dma["high"] == 14
    assert dma["managed"] == 3840


def test_parse_zoneinfo_empty():
    assert mod.parse_zoneinfo("") == []


def test_parse_zoneinfo_normal_zone_at_low():
    zones = mod.parse_zoneinfo(ZONEINFO_SAMPLE)
    normal = next(z for z in zones if z["zone"] == "Normal")
    assert normal["free"] == 10
    assert normal["low"] == 20  # free < low


# --- parse_vmstat --------------------------------------------------

def test_parse_vmstat_basic():
    vm = mod.parse_vmstat(VMSTAT_SAMPLE)
    assert vm["pgsteal_kswapd"] == 500000
    assert vm["pgsteal_direct"] == 5000
    assert vm["compact_fail"] == 5


def test_parse_vmstat_skips_garbage():
    txt = "nr_free 100\ngarbage one two three\nbad notnum\n"
    vm = mod.parse_vmstat(txt)
    assert vm == {"nr_free": 100}


# --- classify ------------------------------------------------------

def _zones(*tuples):
    """tuples = [(zone, free, low), ...]"""
    return [{"node": 0, "zone": z, "free": f, "low": l,
              "min": l - 3, "high": l + 5, "managed": 100000}
            for z, f, l in tuples]


def test_classify_unknown_when_empty():
    v = mod.classify([], {})
    assert v["verdict"] == "unknown"


def test_classify_ok():
    zones = _zones(("Normal", 60000, 200))
    vm = {"pgsteal_kswapd": 1000000, "pgsteal_direct": 100,
            "compact_success": 100, "compact_fail": 5}
    v = mod.classify(zones, vm)
    assert v["verdict"] == "ok"


def test_classify_direct_reclaim_active():
    zones = _zones(("Normal", 60000, 200))
    vm = {"pgsteal_kswapd": 100000, "pgsteal_direct": 50000,
            "compact_success": 100, "compact_fail": 0}
    v = mod.classify(zones, vm)
    assert v["verdict"] == "direct_reclaim_active"
    assert "watermark_scale_factor" in v["recommendation"]


def test_classify_direct_reclaim_skipped_below_threshold():
    # 5 % direct vs kswapd — below 10 % threshold, not flagged.
    zones = _zones(("Normal", 60000, 200))
    vm = {"pgsteal_kswapd": 1000000, "pgsteal_direct": 50000,
            "compact_success": 100, "compact_fail": 0}
    v = mod.classify(zones, vm)
    assert v["verdict"] == "ok"


def test_classify_compaction_failures():
    zones = _zones(("Normal", 60000, 200))
    vm = {"pgsteal_kswapd": 1000000, "pgsteal_direct": 0,
            "compact_success": 100, "compact_fail": 50}
    v = mod.classify(zones, vm)
    assert v["verdict"] == "compaction_failures"
    assert "transparent_hugepage" in v["recommendation"]


def test_classify_zone_low():
    # Normal zone at low watermark.
    zones = _zones(("Normal", 10, 20))
    vm = {"pgsteal_kswapd": 1000000, "pgsteal_direct": 100,
            "compact_success": 100, "compact_fail": 5}
    v = mod.classify(zones, vm)
    assert v["verdict"] == "zone_low"
    assert "Normal" in v["reason"]


def test_classify_direct_reclaim_wins_over_compaction():
    zones = _zones(("Normal", 60000, 200))
    vm = {"pgsteal_kswapd": 100000, "pgsteal_direct": 50000,
            "compact_success": 10, "compact_fail": 50}
    v = mod.classify(zones, vm)
    assert v["verdict"] == "direct_reclaim_active"


def test_classify_compaction_wins_over_zone_low():
    zones = _zones(("Normal", 10, 20))
    vm = {"pgsteal_kswapd": 1000000, "pgsteal_direct": 100,
            "compact_success": 10, "compact_fail": 50}
    v = mod.classify(zones, vm)
    assert v["verdict"] == "compaction_failures"


# --- status integration --------------------------------------------

def test_status_with_isolated_files(monkeypatch, tmp_path):
    (tmp_path / "zoneinfo").write_text(ZONEINFO_SAMPLE)
    (tmp_path / "vmstat").write_text(VMSTAT_SAMPLE)
    monkeypatch.setattr(mod, "_PROC_ZONEINFO",
                        str(tmp_path / "zoneinfo"))
    monkeypatch.setattr(mod, "_PROC_VMSTAT",
                        str(tmp_path / "vmstat"))
    out = mod.status()
    assert out["ok"] is True
    assert out["zone_count"] == 3
    # 5000/505000 ≈ 1 % direct → not flagged ; Normal zone free=10
    # < low=20 → zone_low wins.
    assert out["verdict"]["verdict"] == "zone_low"


def test_status_unknown_when_files_missing(monkeypatch, tmp_path):
    monkeypatch.setattr(mod, "_PROC_ZONEINFO",
                        str(tmp_path / "nope"))
    monkeypatch.setattr(mod, "_PROC_VMSTAT",
                        str(tmp_path / "nope2"))
    out = mod.status()
    assert out["ok"] is False
    assert out["verdict"]["verdict"] == "unknown"
