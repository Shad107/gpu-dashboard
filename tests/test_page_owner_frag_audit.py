"""Tests for modules/page_owner_frag_audit.py — R&D #82.4."""
from __future__ import annotations

import os

import pytest

from gpu_dashboard.modules import page_owner_frag_audit as mod


def _extfrag_text(zones):
    """zones: list of (node, zone, values list)."""
    lines = []
    for node, zone, vals in zones:
        v = " ".join(f"{x:.3f}" for x in vals)
        lines.append(f"Node {node}, zone {zone} {v}")
    return "\n".join(lines) + "\n"


# --- parse_index -----------------------------------------------

def test_parse_index_empty():
    assert mod.parse_index("") == []


def test_parse_index_basic():
    text = _extfrag_text([
        (0, "Normal",
         [-1.0, -1.0, -1.0, -1.0, 0.0, 0.5, 0.8, 0.95,
          0.99, 1.0, 1.0]),
    ])
    rows = mod.parse_index(text)
    assert len(rows) == 1
    assert rows[0]["zone"] == "Normal"
    assert rows[0]["values"][7] == 0.95


def test_parse_index_skips_garbage():
    text = ("garbage line\n"
              "Node 0, zone Normal 0.0 0.5 0.9\n"
              "another bad line\n")
    rows = mod.parse_index(text)
    assert len(rows) == 1


# --- read_thp_defrag -------------------------------------------

def test_read_thp_defrag_basic(tmp_path):
    p = tmp_path / "defrag"
    p.write_text(
        "[always] defer defer+madvise madvise never\n")
    assert mod.read_thp_defrag(str(p)) == "always"


def test_read_thp_defrag_madvise(tmp_path):
    p = tmp_path / "defrag"
    p.write_text(
        "always defer defer+madvise [madvise] never\n")
    assert mod.read_thp_defrag(str(p)) == "madvise"


def test_read_thp_defrag_missing(tmp_path):
    assert mod.read_thp_defrag(
        str(tmp_path / "nope")) is None


# --- _max_index ------------------------------------------------

def test_max_index_filters_by_zone():
    rows = [
        {"node": 0, "zone": "DMA",
            "values": [0.99] * 11},  # ignored zone
        {"node": 0, "zone": "Normal",
            "values": [-1.0] * 4 + [0.5, 0.95]},
    ]
    out = mod._max_index(rows, 4)
    assert out["zone"] == "Normal"
    assert out["order"] == 5
    assert out["value"] == 0.95


def test_max_index_skips_negative():
    rows = [{"node": 0, "zone": "Normal",
              "values": [-1.0] * 11}]
    assert mod._max_index(rows, 4) is None


def test_max_index_respects_min_order():
    rows = [{"node": 0, "zone": "Normal",
              "values": [0.99, 0.99, 0.0, 0.0]}]
    assert mod._max_index(rows, 4) is None


# --- classify --------------------------------------------------

def test_classify_requires_root_when_no_data():
    v = mod.classify(None, None, False, False, None)
    assert v["verdict"] == "requires_root"


def test_classify_ok_clean_indices():
    ef = _extfrag_text([
        (0, "Normal", [-1.0] * 11),
        (0, "DMA32", [-1.0] * 11),
    ])
    un = _extfrag_text([
        (0, "Normal", [0.0] * 11),
    ])
    v = mod.classify(ef, un, False, False, "madvise")
    assert v["verdict"] == "ok"


def test_classify_extfrag_high_with_thp_defrag():
    ef = _extfrag_text([
        (0, "Normal",
         [-1.0, -1.0, -1.0, -1.0, 0.5, 0.7, 0.95]),
    ])
    v = mod.classify(ef, None, False, False, "always")
    assert v["verdict"] == "extfrag_high_with_thp_defrag"
    assert v["order"] >= 4


def test_classify_extfrag_high_thp_madvise_falls_through():
    # high extfrag but THP defrag = madvise (not aggressive)
    ef = _extfrag_text([
        (0, "Normal",
         [-1.0, -1.0, -1.0, -1.0, 0.5, 0.7, 0.95]),
    ])
    un = _extfrag_text([
        (0, "Normal", [0.0] * 11),
    ])
    v = mod.classify(ef, un, False, False, "madvise")
    # high extfrag alone doesn't fire err — needs aggressive THP
    assert v["verdict"] == "ok"


def test_classify_unusable_index_high():
    ef = _extfrag_text([
        (0, "Normal", [-1.0] * 11),
    ])
    un = _extfrag_text([
        (0, "Normal",
         [0.0, 0.0, 0.0, 0.8, 0.9]),
    ])
    v = mod.classify(ef, un, False, False, "madvise")
    assert v["verdict"] == "unusable_index_high"


def test_classify_page_owner_overhead():
    ef = _extfrag_text([(0, "Normal", [-1.0] * 11)])
    un = _extfrag_text([(0, "Normal", [0.0] * 11)])
    v = mod.classify(ef, un, True, True, "madvise")
    assert v["verdict"] == "page_owner_overhead_no_use"


def test_classify_page_owner_not_readable_ok():
    ef = _extfrag_text([(0, "Normal", [-1.0] * 11)])
    un = _extfrag_text([(0, "Normal", [0.0] * 11)])
    v = mod.classify(ef, un, True, False, "madvise")
    assert v["verdict"] == "ok"


# Priority : extfrag+thp > unusable > page_owner
def test_priority_extfrag_over_unusable():
    ef = _extfrag_text([
        (0, "Normal", [-1.0, -1.0, -1.0, -1.0, 0.95])
    ])
    un = _extfrag_text([
        (0, "Normal", [0.0, 0.0, 0.0, 0.9])
    ])
    v = mod.classify(ef, un, False, False, "always")
    assert v["verdict"] == "extfrag_high_with_thp_defrag"


def test_priority_unusable_over_page_owner():
    ef = _extfrag_text([(0, "Normal", [-1.0] * 11)])
    un = _extfrag_text([
        (0, "Normal", [0.0, 0.0, 0.0, 0.85])
    ])
    v = mod.classify(ef, un, True, True, "madvise")
    assert v["verdict"] == "unusable_index_high"


# --- status integration ----------------------------------------

def test_status_na_no_debugfs(tmp_path):
    out = mod.status(None, str(tmp_path / "nope"),
                       str(tmp_path / "nope_thp"))
    assert out["verdict"]["verdict"] == "n/a"


def test_status_na_no_extfrag_in_debug(tmp_path):
    # debugfs exists but no extfrag dir, no page_owner
    debug = tmp_path / "debug"
    debug.mkdir()
    out = mod.status(None, str(debug),
                       str(tmp_path / "nope_thp"))
    assert out["verdict"]["verdict"] == "n/a"


def test_status_ok_synthetic(tmp_path):
    debug = tmp_path / "debug"
    extfrag = debug / "extfrag"
    extfrag.mkdir(parents=True)
    (extfrag / "extfrag_index").write_text(
        _extfrag_text([(0, "Normal", [-1.0] * 11)]))
    (extfrag / "unusable_index").write_text(
        _extfrag_text([(0, "Normal", [0.0] * 11)]))
    thp = tmp_path / "defrag"
    thp.write_text(
        "always defer defer+madvise [madvise] never\n")
    out = mod.status(None, str(debug), str(thp))
    assert out["ok"] is True
    assert out["thp_defrag"] == "madvise"
    assert out["extfrag_zones"] == 1
    assert out["verdict"]["verdict"] == "ok"


def test_status_extfrag_high_synthetic(tmp_path):
    debug = tmp_path / "debug"
    extfrag = debug / "extfrag"
    extfrag.mkdir(parents=True)
    (extfrag / "extfrag_index").write_text(
        _extfrag_text([
            (0, "Normal",
             [-1.0, -1.0, -1.0, -1.0, 0.5, 0.95])]))
    (extfrag / "unusable_index").write_text(
        _extfrag_text([(0, "Normal", [0.0] * 11)]))
    thp = tmp_path / "defrag"
    thp.write_text(
        "[always] defer defer+madvise madvise never\n")
    out = mod.status(None, str(debug), str(thp))
    assert out["ok"] is False
    assert (out["verdict"]["verdict"]
            == "extfrag_high_with_thp_defrag")
