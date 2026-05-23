"""Tests for modules/memory_hotplug_audit.py — R&D #62.3."""
from __future__ import annotations

import pytest

from gpu_dashboard.modules import memory_hotplug_audit as mod


def _mk_block(root, idx, *, state="online", valid_zones="Normal",
                removable=1):
    d = root / f"memory{idx}"
    d.mkdir(parents=True, exist_ok=True)
    (d / "state").write_text(state + "\n")
    (d / "valid_zones").write_text(valid_zones + "\n")
    (d / "removable").write_text(f"{removable}\n")


# --- _is_movable_only -------------------------------------------

def test_is_movable_only_yes():
    assert mod._is_movable_only("Movable") is True


def test_is_movable_only_no():
    assert mod._is_movable_only("Normal Movable") is False
    assert mod._is_movable_only("Normal") is False
    assert mod._is_movable_only("none") is False
    assert mod._is_movable_only("") is False
    assert mod._is_movable_only(None) is False


# --- list_memory_blocks -----------------------------------------

def test_list_memory_blocks_missing(tmp_path):
    assert mod.list_memory_blocks(str(tmp_path / "nope")) == []


def test_list_memory_blocks(tmp_path):
    _mk_block(tmp_path, 0)
    _mk_block(tmp_path, 100, state="offline")
    (tmp_path / "block_size_bytes").write_text("8000000\n")
    out = mod.list_memory_blocks(str(tmp_path))
    assert len(out) == 2
    states = sorted(b["state"] for b in out)
    assert states == ["offline", "online"]


# --- read_meminfo_total_kib -------------------------------------

def test_read_meminfo_total_kib(tmp_path):
    p = tmp_path / "meminfo"
    p.write_text("MemTotal:    32768000 kB\nMemFree: 1\n")
    assert mod.read_meminfo_total_kib(str(p)) == 32768000


def test_read_meminfo_total_kib_missing(tmp_path):
    assert mod.read_meminfo_total_kib(
        str(tmp_path / "nope")) is None


# --- classify ---------------------------------------------------

def _b(id_="memory0", state="online", zones="Normal", removable=1):
    return {"id": id_, "state": state, "valid_zones": zones,
              "removable": removable}


def test_classify_unsupported():
    v = mod.classify([], 0x8000000, 32_000_000,
                       sys_memory_present=False)
    assert v["verdict"] == "unsupported"


def test_classify_unknown_no_blocks():
    v = mod.classify([], 0x8000000, 32_000_000,
                       sys_memory_present=True)
    assert v["verdict"] == "unknown"


def test_classify_ok():
    v = mod.classify([_b()], 0x8000000, 32_000_000,
                       sys_memory_present=True)
    assert v["verdict"] == "ok"


def test_classify_offline():
    v = mod.classify([_b(state="offline")], 0x8000000,
                       32_000_000, sys_memory_present=True)
    assert v["verdict"] == "offline_blocks_present"


def test_classify_non_removable_in_movable():
    v = mod.classify([_b(zones="Movable", removable=0)],
                       0x8000000, 32_000_000,
                       sys_memory_present=True)
    assert v["verdict"] == "non_removable_in_movable"


def test_classify_movable_skew():
    # 6/10 = 60 % > 50 %
    blocks = [_b(id_=f"memory{i}", zones="Movable", removable=1)
                  for i in range(6)]
    blocks += [_b(id_=f"memory{i+6}", zones="Normal")
                  for i in range(4)]
    v = mod.classify(blocks, 0x8000000, 32_000_000,
                       sys_memory_present=True)
    assert v["verdict"] == "movable_only_zone_skew"


def test_classify_priority_offline_wins():
    v = mod.classify(
        [_b(state="offline"),
         _b(id_="memory1", zones="Movable", removable=0)],
        0x8000000, 32_000_000, sys_memory_present=True)
    assert v["verdict"] == "offline_blocks_present"


# --- status integration -----------------------------------------

def test_status_unsupported(tmp_path):
    out = mod.status(None, str(tmp_path / "nope"),
                       str(tmp_path / "nomem"))
    assert out["ok"] is False
    assert out["verdict"]["verdict"] == "unsupported"


def test_status_live_like(tmp_path):
    sm = tmp_path / "mem"
    sm.mkdir()
    (sm / "block_size_bytes").write_text("0x8000000\n")
    _mk_block(sm, 0)
    _mk_block(sm, 1, valid_zones="Normal")
    mi = tmp_path / "meminfo"
    mi.write_text("MemTotal: 32768000 kB\n")
    out = mod.status(None, str(sm), str(mi))
    assert out["ok"] is True
    assert out["block_count"] == 2
    assert out["mem_total_kib"] == 32768000
    assert out["verdict"]["verdict"] == "ok"
