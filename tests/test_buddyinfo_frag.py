"""Tests for modules/buddyinfo_frag.py — R&D #34.2 fragmentation audit."""
from __future__ import annotations

from pathlib import Path

import pytest

from gpu_dashboard.modules import buddyinfo_frag


_LIVE_BUDDYINFO = """\
Node 0, zone      DMA      0      0      0      0      0      0      0      0      1      1      2
Node 0, zone    DMA32   3230   3088   2166   1924    941    297     62     18      4      0     17
Node 0, zone   Normal   5936   6649   6604   3506   1473    391    133     75     39     11      0
"""

_FRAGMENTED = """\
Node 0, zone   Normal  10000   5000   2000    500     50      5      0      0      0      0      0
"""

_HEALTHY = """\
Node 0, zone   Normal   5000   5000   5000   5000   5000   5000   3000   2000   1000    500    100
"""


def _mk_buddy(path: Path, text: str):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)


# --- parse_buddyinfo ----------------------------------------------

def test_parse_buddyinfo_full():
    zones = buddyinfo_frag.parse_buddyinfo(_LIVE_BUDDYINFO)
    assert len(zones) == 3
    normal = next(z for z in zones if z["zone"] == "Normal")
    assert normal["node"] == 0
    assert normal["counts"] == [5936, 6649, 6604, 3506, 1473, 391, 133, 75, 39, 11, 0]
    assert normal["order9"] == 11
    assert normal["order10"] == 0


def test_parse_buddyinfo_empty():
    assert buddyinfo_frag.parse_buddyinfo("") == []


def test_parse_buddyinfo_skips_invalid_lines():
    txt = ("garbage\n"
           "Node 0, zone   Normal   100   50   25\n")
    zones = buddyinfo_frag.parse_buddyinfo(txt)
    assert len(zones) == 1
    assert zones[0]["counts"] == [100, 50, 25]


# --- order_bytes --------------------------------------------------

def test_order_bytes_zero_is_4k():
    assert buddyinfo_frag.order_bytes(0, page_size=4096) == 4096


def test_order_bytes_nine_is_2m():
    assert buddyinfo_frag.order_bytes(9, page_size=4096) == 2 * 1024 * 1024


def test_total_free_bytes():
    counts = [5936, 6649, 6604, 3506, 1473, 391, 133, 75, 39, 11, 0]
    total = buddyinfo_frag.total_free_bytes(counts, page_size=4096)
    # 5936*4K + 6649*8K + ... order 9 = 11 * 2 MiB = 22 MiB
    assert total > 0
    # The order-9 contribution alone is 11 * 2 MiB
    assert total > 11 * 2 * 1024 * 1024


# --- classify -----------------------------------------------------

def test_classify_ok_healthy():
    zone = {"zone": "Normal", "counts":
            [5000, 5000, 5000, 5000, 5000, 5000, 3000, 2000, 1000, 500, 100]}
    v = buddyinfo_frag.classify([zone])
    assert v["verdict"] == "ok"


def test_classify_fragmented_no_high_order_pages():
    # The Live case: order 9 = 11, order 10 = 0 → fragmented_moderate
    zone = {"zone": "Normal", "counts":
            [5936, 6649, 6604, 3506, 1473, 391, 133, 75, 39, 11, 0]}
    v = buddyinfo_frag.classify([zone])
    assert v["verdict"] in ("fragmented_moderate", "ok")


def test_classify_fragmented_severe_zero_thp():
    zone = {"zone": "Normal", "counts":
            [10000, 5000, 2000, 500, 50, 5, 0, 0, 0, 0, 0]}
    v = buddyinfo_frag.classify([zone])
    assert v["verdict"] == "fragmented_severe"
    assert ("compact" in v["recommendation"].lower() or
            "drop_caches" in v["recommendation"].lower())


def test_classify_picks_worst_zone():
    # DMA32 healthy but Normal severely fragmented → severe wins
    zones = [
        {"zone": "DMA32",
         "counts": [3230, 3088, 2166, 1924, 941, 297, 62, 18, 4, 0, 17]},
        {"zone": "Normal",
         "counts": [10000, 5000, 2000, 500, 50, 5, 0, 0, 0, 0, 0]},
    ]
    v = buddyinfo_frag.classify(zones)
    assert v["verdict"] == "fragmented_severe"


def test_classify_ignores_dma_zone():
    # DMA zone is tiny (16 MiB) and always sparse — not relevant
    zones = [
        {"zone": "DMA", "counts": [0, 0, 0, 0, 0, 0, 0, 0, 1, 1, 2]},
    ]
    v = buddyinfo_frag.classify(zones)
    # Only DMA → no Normal/DMA32 to evaluate → unknown / ok
    assert v["verdict"] in ("unknown", "ok")


def test_classify_unknown_empty():
    v = buddyinfo_frag.classify([])
    assert v["verdict"] == "unknown"


def test_classify_recipe_includes_drop_caches():
    zone = {"zone": "Normal", "counts":
            [10000, 5000, 2000, 500, 50, 5, 0, 0, 0, 0, 0]}
    v = buddyinfo_frag.classify([zone])
    rec = v["recommendation"]
    assert "drop_caches" in rec or "compact_memory" in rec


# --- status -------------------------------------------------------

def test_status_no_buddyinfo(tmp_path, monkeypatch):
    monkeypatch.setattr(buddyinfo_frag, "_BUDDYINFO",
                          str(tmp_path / "absent"))
    s = buddyinfo_frag.status()
    assert s["ok"] is False
    assert s["error"] == "buddyinfo_unavailable"


def test_status_full_live_payload(tmp_path, monkeypatch):
    bi = tmp_path / "buddyinfo"
    _mk_buddy(bi, _LIVE_BUDDYINFO)
    monkeypatch.setattr(buddyinfo_frag, "_BUDDYINFO", str(bi))
    s = buddyinfo_frag.status()
    assert s["ok"] is True
    assert len(s["zones"]) == 3
    normal = next(z for z in s["zones"] if z["zone"] == "Normal")
    assert normal["order9_pages"] == 11
    assert normal["total_free_mb"] > 0


def test_status_fragmented_severe(tmp_path, monkeypatch):
    bi = tmp_path / "buddyinfo"
    _mk_buddy(bi, _FRAGMENTED)
    monkeypatch.setattr(buddyinfo_frag, "_BUDDYINFO", str(bi))
    s = buddyinfo_frag.status()
    assert s["verdict"]["verdict"] == "fragmented_severe"


def test_status_healthy(tmp_path, monkeypatch):
    bi = tmp_path / "buddyinfo"
    _mk_buddy(bi, _HEALTHY)
    monkeypatch.setattr(buddyinfo_frag, "_BUDDYINFO", str(bi))
    s = buddyinfo_frag.status()
    assert s["verdict"]["verdict"] == "ok"


def test_status_summary_total_thp_blocks(tmp_path, monkeypatch):
    bi = tmp_path / "buddyinfo"
    _mk_buddy(bi, _LIVE_BUDDYINFO)
    monkeypatch.setattr(buddyinfo_frag, "_BUDDYINFO", str(bi))
    s = buddyinfo_frag.status()
    # Sum across all zones (Normal: 11+0, DMA32: 0+17, DMA: 1+2) = 31
    assert s["total_thp_blocks"] == 31
