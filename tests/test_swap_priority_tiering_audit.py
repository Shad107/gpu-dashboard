"""Tests for modules/swap_priority_tiering_audit.py R&D #110.1."""
from __future__ import annotations

import pytest

from gpu_dashboard.modules import swap_priority_tiering_audit as mod


def test_parse_swaps_header_only():
    text = "Filename\tType\tSize\tUsed\tPriority\n"
    assert mod.parse_swaps(text) == []


def test_parse_swaps_one():
    text = (
        "Filename Type Size Used Priority\n"
        "/swap.img file 8388604 0 -2\n")
    out = mod.parse_swaps(text)
    assert len(out) == 1
    assert out[0]["filename"] == "/swap.img"
    assert out[0]["priority"] == -2


def test_is_zram():
    assert mod.is_zram("/dev/zram0") is True
    assert mod.is_zram("/dev/sda2") is False


def test_classify_unknown():
    v = mod.classify(False, [])
    assert v["verdict"] == "unknown"


def test_classify_ok_no_swap():
    v = mod.classify(True, [])
    assert v["verdict"] == "ok"


def test_classify_ok_single():
    v = mod.classify(True, [
        {"filename": "/swap.img", "type": "file",
         "size": 1, "used": 0, "priority": -2}])
    assert v["verdict"] == "ok"


def test_classify_disk_higher_than_zram_warn():
    v = mod.classify(True, [
        {"filename": "/dev/zram0", "type": "partition",
         "size": 1, "used": 0, "priority": 10},
        {"filename": "/swap.img", "type": "file",
         "size": 1, "used": 0, "priority": 20}])
    assert v["verdict"] == "disk_swap_higher_than_zram"


def test_classify_equal_priority_accent():
    v = mod.classify(True, [
        {"filename": "/dev/sda1", "type": "partition",
         "size": 1, "used": 0, "priority": 5},
        {"filename": "/dev/sdb1", "type": "partition",
         "size": 1, "used": 0, "priority": 5}])
    assert v["verdict"] == "equal_priority_round_robin"


def test_classify_zram_higher_is_ok():
    v = mod.classify(True, [
        {"filename": "/dev/zram0", "type": "partition",
         "size": 1, "used": 0, "priority": 100},
        {"filename": "/swap.img", "type": "file",
         "size": 1, "used": 0, "priority": -2}])
    assert v["verdict"] == "ok"


def test_status_unknown(tmp_path):
    out = mod.status(None, str(tmp_path / "nope"))
    assert out["verdict"]["verdict"] == "unknown"


def test_status_single(tmp_path):
    p = tmp_path / "swaps"
    p.write_text(
        "Filename Type Size Used Priority\n"
        "/swap.img file 8388604 0 -2\n")
    out = mod.status(None, str(p))
    assert out["verdict"]["verdict"] == "ok"
    assert out["swap_count"] == 1


def test_status_inversion(tmp_path):
    p = tmp_path / "swaps"
    p.write_text(
        "Filename Type Size Used Priority\n"
        "/dev/zram0 partition 1024 0 5\n"
        "/swap.img file 1024 0 10\n")
    out = mod.status(None, str(p))
    assert (out["verdict"]["verdict"]
            == "disk_swap_higher_than_zram")
