"""Tests for modules/zswap_zram_audit.py — R&D #41.1."""
from __future__ import annotations

from pathlib import Path

import pytest

from gpu_dashboard.modules import zswap_zram_audit as mod


# --- read_zswap ----------------------------------------------------

def _mk_zswap(root: Path, **fields):
    root.mkdir(parents=True, exist_ok=True)
    for k, v in fields.items():
        (root / k).write_text(str(v) + "\n")


def test_read_zswap_missing(tmp_path):
    assert mod.read_zswap(str(tmp_path / "nope")) == {"available": False}


def test_read_zswap_basic(tmp_path):
    root = tmp_path / "z"
    _mk_zswap(root, enabled="Y", compressor="lz4", zpool="zsmalloc",
              max_pool_percent=40, accept_threshold_percent=90)
    s = mod.read_zswap(str(root))
    assert s["available"] is True
    assert s["enabled"] is True
    assert s["compressor"] == "lz4"
    assert s["zpool"] == "zsmalloc"
    assert s["max_pool_percent"] == 40


def test_read_zswap_disabled(tmp_path):
    root = tmp_path / "z"
    _mk_zswap(root, enabled="N", compressor="lzo", zpool="zsmalloc",
              max_pool_percent=20)
    s = mod.read_zswap(str(root))
    assert s["enabled"] is False


def test_read_zswap_unparseable(tmp_path):
    root = tmp_path / "z"
    _mk_zswap(root, enabled="garbage")
    s = mod.read_zswap(str(root))
    assert s["enabled"] is None


# --- read_zram_devices ---------------------------------------------

def test_read_zram_devices_empty(tmp_path):
    sys_block = tmp_path / "block"
    sys_block.mkdir()
    (sys_block / "sda").mkdir()  # not a zram
    assert mod.read_zram_devices(str(sys_block)) == []


def test_read_zram_devices_one(tmp_path):
    sys_block = tmp_path / "block"
    sys_block.mkdir()
    z = sys_block / "zram0"
    z.mkdir()
    (z / "disksize").write_text("8589934592\n")
    (z / "comp_algorithm").write_text("[lz4] zstd lzo\n")
    (z / "max_comp_streams").write_text("4\n")
    devs = mod.read_zram_devices(str(sys_block))
    assert len(devs) == 1
    assert devs[0]["name"] == "zram0"
    assert devs[0]["disksize"] == 8589934592
    assert "lz4" in devs[0]["comp_algorithm"]


def test_read_zram_devices_missing_root(tmp_path):
    assert mod.read_zram_devices(str(tmp_path / "nope")) == []


# --- read_swap_devices ---------------------------------------------

def test_read_swap_devices_basic(tmp_path):
    p = tmp_path / "swaps"
    p.write_text("Filename\tType\tSize\tUsed\tPriority\n"
                  "/swap.img\tfile\t8388604\t1000\t-2\n")
    devs = mod.read_swap_devices(str(p))
    assert len(devs) == 1
    assert devs[0]["path"] == "/swap.img"
    assert devs[0]["size_kb"] == 8388604


def test_read_swap_devices_empty(tmp_path):
    p = tmp_path / "swaps"
    p.write_text("Filename\tType\tSize\tUsed\tPriority\n")
    assert mod.read_swap_devices(str(p)) == []


def test_read_swap_devices_missing(tmp_path):
    assert mod.read_swap_devices(str(tmp_path / "nope")) == []


# --- read_mem_total_gb --------------------------------------------

def test_read_mem_total_gb(tmp_path):
    p = tmp_path / "meminfo"
    p.write_text("MemTotal:       32115212 kB\nMemFree:    5 kB\n")
    gb = mod.read_mem_total_gb(str(p))
    assert gb is not None and 30 < gb < 32


def test_read_mem_total_gb_missing(tmp_path):
    assert mod.read_mem_total_gb(str(tmp_path / "nope")) is None


# --- classify ------------------------------------------------------

def _zswap(**overrides):
    base = {"available": True, "enabled": True, "compressor": "lz4",
             "zpool": "zsmalloc", "max_pool_percent": 40,
             "accept_threshold_percent": 90}
    base.update(overrides)
    return base


def _swap_disk():
    return [{"path": "/swap.img", "type": "file",
              "size_kb": 8388604, "used_kb": 5000000,
              "priority": "-2"}]


def test_classify_unknown_when_no_module():
    v = mod.classify({"available": False}, [], [], 32)
    assert v["verdict"] == "unknown"


def test_classify_ok_not_needed_when_no_swap_and_big_ram():
    v = mod.classify(_zswap(enabled=False), [], [], 128)
    assert v["verdict"] == "ok_not_needed"


def test_classify_zswap_disabled_on_tight_box():
    v = mod.classify(_zswap(enabled=False), [], _swap_disk(), 32)
    assert v["verdict"] == "zswap_disabled_on_tight_box"
    assert "lz4" in v["recommendation"]


def test_classify_legacy_compressor():
    v = mod.classify(_zswap(compressor="lzo"), [], _swap_disk(), 32)
    assert v["verdict"] == "legacy_compressor"
    assert "lz4" in v["recommendation"]


def test_classify_legacy_zpool():
    v = mod.classify(_zswap(zpool="z3fold"), [], _swap_disk(), 32)
    assert v["verdict"] == "legacy_compressor"


def test_classify_pool_too_small_on_tight_box():
    v = mod.classify(_zswap(max_pool_percent=20),
                       [], _swap_disk(), 32)
    assert v["verdict"] == "pool_too_small"
    assert "40" in v["recommendation"]


def test_classify_pool_ok_on_big_box():
    # 128-GB box with same 20 % is not flagged as tight.
    v = mod.classify(_zswap(max_pool_percent=20),
                       [], _swap_disk(), 128)
    # On big box + modern compressor + 20% pool, this is OK
    # (not "tight"). Verdict should be ok_configured.
    assert v["verdict"] == "ok_configured"


def test_classify_zram_idle_when_useful():
    zram = [{"name": "zram0", "disksize": 4 * 1024**3,
              "comp_algorithm": "lz4", "max_comp_streams": 4,
              "mm_stat_raw": None}]
    v = mod.classify(_zswap(), zram, _swap_disk(), 32)
    assert v["verdict"] == "zram_idle_when_useful"
    assert "swapon" in v["recommendation"]


def test_classify_zram_uninitialized_ignored():
    # disksize=0 means zram device exists but isn't sized → ignore
    zram = [{"name": "zram0", "disksize": 0,
              "comp_algorithm": "lz4", "max_comp_streams": 4,
              "mm_stat_raw": None}]
    v = mod.classify(_zswap(), zram, _swap_disk(), 32)
    assert v["verdict"] == "ok_configured"


def test_classify_ok_configured_when_all_modern():
    v = mod.classify(_zswap(), [], _swap_disk(), 32)
    assert v["verdict"] == "ok_configured"


def test_classify_disabled_priority_over_legacy():
    # zswap.enabled=N is more important than compressor=lzo on
    # a tight box — flag the off state first.
    v = mod.classify(_zswap(enabled=False, compressor="lzo"),
                       [], _swap_disk(), 32)
    assert v["verdict"] == "zswap_disabled_on_tight_box"


# --- status integration -------------------------------------------

def test_status_with_isolated_roots(monkeypatch, tmp_path):
    sys_z = tmp_path / "z"
    _mk_zswap(sys_z, enabled="N", compressor="lzo", zpool="zsmalloc",
              max_pool_percent=20, accept_threshold_percent=90)
    sys_block = tmp_path / "block"
    sys_block.mkdir()
    swaps = tmp_path / "swaps"
    swaps.write_text("Filename\tType\tSize\tUsed\tPriority\n"
                      "/swap.img\tfile\t8388604\t6000000\t-2\n")
    meminfo = tmp_path / "meminfo"
    meminfo.write_text("MemTotal:       32115212 kB\n")
    monkeypatch.setattr(mod, "_SYS_ZSWAP", str(sys_z))
    monkeypatch.setattr(mod, "_SYS_BLOCK", str(sys_block))
    monkeypatch.setattr(mod, "_PROC_SWAPS", str(swaps))
    monkeypatch.setattr(mod, "_MEMINFO", str(meminfo))
    out = mod.status()
    assert out["ok"] is True
    assert out["zswap"]["enabled"] is False
    assert out["verdict"]["verdict"] == "zswap_disabled_on_tight_box"


def test_status_unknown_when_no_module(monkeypatch, tmp_path):
    monkeypatch.setattr(mod, "_SYS_ZSWAP", str(tmp_path / "nope"))
    monkeypatch.setattr(mod, "_SYS_BLOCK", str(tmp_path / "block"))
    monkeypatch.setattr(mod, "_PROC_SWAPS", str(tmp_path / "swaps"))
    monkeypatch.setattr(mod, "_MEMINFO", str(tmp_path / "mem"))
    out = mod.status()
    assert out["ok"] is False
    assert out["verdict"]["verdict"] == "unknown"
