"""Tests for modules/pagetypeinfo_audit.py — R&D #57.2."""
from __future__ import annotations

import pytest

from gpu_dashboard.modules import pagetypeinfo_audit as mod


PAGETYPE_HEALTHY = """\
Page block order: 9
Pages per block:  512

Free pages count per migrate type at order       0      1      2      3      4      5      6      7      8      9     10
Node    0, zone    DMA32, type    Unmovable      0      0      0      0      0      0      0      0      0      1      1
Node    0, zone    DMA32, type      Movable    100    200    100     50     30     20     10      8      5      4      3
Node    0, zone    DMA32, type  Reclaimable     0      0      0      0      0      0      0      0      0      0      0
Node    0, zone    DMA32, type   HighAtomic     0      0      0      0      0      0      0      0      0      0      0
Node    0, zone    DMA32, type     Isolate      0      0      0      0      0      0      0      0      0      0      0

Number of blocks type     Unmovable      Movable  Reclaimable   HighAtomic      Isolate
Node 0, zone     DMA32          5        7000          1          0          0
"""


PAGETYPE_STARVED = """\
Page block order: 9
Pages per block:  512

Free pages count per migrate type at order       0      1      2      3      4      5      6      7      8      9     10
Node    0, zone    DMA32, type      Movable      0      0      0      0      0      0      0      0      0      0      0

Number of blocks type     Unmovable      Movable  Reclaimable   HighAtomic      Isolate
Node 0, zone     DMA32          5        7000          1          0          0
"""

PAGETYPE_POLLUTED = """\
Page block order: 9
Pages per block:  512

Free pages count per migrate type at order       0      1      2      3      4      5      6      7      8      9     10
Node    0, zone    DMA32, type      Movable    100    200    100     50     30     20     10      8      5      4      3

Number of blocks type     Unmovable      Movable  Reclaimable   HighAtomic      Isolate
Node 0, zone     DMA32        500        7000          1          0          0
"""


# --- parse_pagetypeinfo -----------------------------------------

def test_parse_empty():
    out = mod.parse_pagetypeinfo("")
    assert out == {"free_pages": [], "block_counts": [],
                       "block_type_header": []}


def test_parse_healthy():
    out = mod.parse_pagetypeinfo(PAGETYPE_HEALTHY)
    assert len(out["free_pages"]) == 5
    assert out["free_pages"][1]["type"] == "Movable"
    assert out["free_pages"][1]["orders"][0] == 100
    assert len(out["block_counts"]) == 1
    assert out["block_counts"][0]["types"]["Movable"] == 7000


def test_parse_starved():
    out = mod.parse_pagetypeinfo(PAGETYPE_STARVED)
    assert len(out["free_pages"]) == 1
    assert sum(out["free_pages"][0]["orders"]) == 0


# --- classify ---------------------------------------------------

def test_classify_unknown():
    v = mod.classify({"free_pages": [], "block_counts": [],
                        "block_type_header": []},
                       perm_denied=False, extfrag_threshold=500)
    assert v["verdict"] == "unknown"


def test_classify_requires_root():
    v = mod.classify({"free_pages": [], "block_counts": []},
                       perm_denied=True, extfrag_threshold=500)
    assert v["verdict"] == "requires_root"


def test_classify_ok():
    parsed = mod.parse_pagetypeinfo(PAGETYPE_HEALTHY)
    v = mod.classify(parsed, False, 500)
    assert v["verdict"] == "ok"


def test_classify_starved():
    parsed = mod.parse_pagetypeinfo(PAGETYPE_STARVED)
    v = mod.classify(parsed, False, 500)
    assert v["verdict"] == "high_order_starved"


def test_classify_polluted():
    # Unmovable 500 / Movable 7000 = ~7 % > 5 % threshold
    parsed = mod.parse_pagetypeinfo(PAGETYPE_POLLUTED)
    v = mod.classify(parsed, False, 500)
    assert v["verdict"] == "unmovable_in_movable"


def test_classify_priority_polluted_wins():
    parsed = mod.parse_pagetypeinfo(PAGETYPE_POLLUTED)
    # Even with starved orders, pollution still wins (higher prio)
    parsed["free_pages"] = [{"node": 0, "zone": "DMA32",
                                "type": "Movable",
                                "orders": [0] * 11}]
    v = mod.classify(parsed, False, 500)
    assert v["verdict"] == "unmovable_in_movable"


# --- status integration -----------------------------------------

def test_status_missing(tmp_path):
    out = mod.status(None, str(tmp_path / "nope"),
                       str(tmp_path / "nope2"))
    assert out["ok"] is False
    assert out["verdict"]["verdict"] == "unknown"


def test_status_live_like(tmp_path):
    pti = tmp_path / "pagetypeinfo"
    pti.write_text(PAGETYPE_HEALTHY)
    ef = tmp_path / "extfrag"
    ef.write_text("500\n")
    out = mod.status(None, str(pti), str(ef))
    assert out["ok"] is True
    assert out["extfrag_threshold"] == 500
    assert out["verdict"]["verdict"] == "ok"
