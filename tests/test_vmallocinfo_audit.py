"""Tests for modules/vmallocinfo_audit.py — R&D #67.3."""
from __future__ import annotations

import pytest

from gpu_dashboard.modules import vmallocinfo_audit as mod


# --- parse_line -------------------------------------------------

def test_parse_line_ioremap():
    line = ("0xffffc90000000000-0xffffc90000005000   20480 "
              "io_mapping_map_wc+0x32/0x80 "
              "phys=0x00000000fed00000 ioremap")
    e = mod.parse_line(line)
    assert e["size"] == 20480
    assert e["caller"] == "io_mapping_map_wc"
    assert e["kind"] == "ioremap"


def test_parse_line_vmalloc():
    line = ("0xffffc9000000e000-0xffffc90000020000  73728 "
              "__alloc_skb+0x123/0x300 vmalloc")
    e = mod.parse_line(line)
    assert e["kind"] == "vmalloc"
    assert e["caller"] == "__alloc_skb"


def test_parse_line_blank():
    assert mod.parse_line("") is None
    assert mod.parse_line("   \n") is None


def test_parse_line_no_kind():
    line = "0xffffc... 1234 someweird_func"
    e = mod.parse_line(line)
    assert e["size"] == 1234
    assert e["caller"] == "someweird_func"
    assert e["kind"] == "unknown"


# --- parse_vmallocinfo ------------------------------------------

def test_parse_vmallocinfo_empty():
    assert mod.parse_vmallocinfo("") == []


def test_parse_vmallocinfo_three_lines():
    txt = ("0xffff... 8192 f1+0x1/0x2 ioremap\n"
              "\n"
              "0xffff... 4096 f2+0x1/0x2 vmalloc\n"
              "0xffff... 2048 f3+0x1/0x2 vmap\n")
    out = mod.parse_vmallocinfo(txt)
    assert len(out) == 3
    assert [e["kind"] for e in out] == ["ioremap", "vmalloc", "vmap"]


# --- aggregate --------------------------------------------------

def test_aggregate_empty():
    a = mod.aggregate([])
    assert a == {"total_bytes": 0, "count": 0,
                    "by_kind": {}, "top_callers": [],
                    "largest": None}


def test_aggregate_basic():
    entries = [
        {"size": 1000, "caller": "f1", "kind": "ioremap"},
        {"size": 5000, "caller": "f2", "kind": "vmalloc"},
        {"size": 3000, "caller": "f1", "kind": "ioremap"},
    ]
    a = mod.aggregate(entries)
    assert a["total_bytes"] == 9000
    assert a["count"] == 3
    assert a["by_kind"] == {"ioremap": 4000, "vmalloc": 5000}
    assert a["largest"]["caller"] == "f2"
    assert a["top_callers"][0]["caller"] == "f2"


# --- classify ---------------------------------------------------

def test_classify_unknown():
    v = mod.classify(False, False, {})
    assert v["verdict"] == "unknown"


def test_classify_requires_root():
    v = mod.classify(True, True, {})
    assert v["verdict"] == "requires_root"


def test_classify_ok():
    a = mod.aggregate(
        [{"size": 1000, "caller": "f", "kind": "vmalloc"},
         {"size": 2000, "caller": "g", "kind": "vmalloc"}])
    v = mod.classify(True, False, a)
    assert v["verdict"] == "ok"


def test_classify_giant_alloc():
    a = mod.aggregate(
        [{"size": 20 * 1024 * 1024, "caller": "leak",
            "kind": "vmalloc"},
         {"size": 1000, "caller": "f", "kind": "vmalloc"}])
    v = mod.classify(True, False, a)
    assert v["verdict"] == "vmalloc_giant_alloc"
    assert "leak" in v["reason"]


def test_classify_fragmentation_count():
    entries = [{"size": 4096, "caller": f"f{i}",
                  "kind": "vmalloc"} for i in range(10_001)]
    a = mod.aggregate(entries)
    v = mod.classify(True, False, a)
    assert v["verdict"] == "vmalloc_fragmentation"


def test_classify_fragmentation_skew():
    # 100 tiny + 1 huge alloc → bottom 80 % sums to a tiny share
    # of total bytes.
    entries = [{"size": 100, "caller": f"f{i}",
                  "kind": "vmalloc"} for i in range(150)]
    entries.append({"size": 100 * 1024 * 1024 - 1,
                       "caller": "fat",
                       "kind": "vmalloc"})
    a = mod.aggregate(entries)
    v = mod.classify(True, False, a)
    assert v["verdict"] == "vmalloc_giant_alloc"
    # (giant takes priority — verify the fragmentation tail check
    #  exists but is correctly ranked behind giant.)


def test_classify_fragmentation_skew_no_giant():
    # 200 small allocs of 100 bytes + 1 large alloc of 1 MB
    # (below 16 MiB threshold). Bottom 80% = 160 entries of 100B
    # = 16 000 bytes. Total ~ 1 MB + 20 000 = ~1 016 000 bytes.
    # 16 000 / 1 016 000 = 1.6 % ≤ 5 % → frag.
    entries = [{"size": 100, "caller": f"f{i}",
                  "kind": "vmalloc"} for i in range(200)]
    entries.append({"size": 1_000_000, "caller": "fat",
                       "kind": "vmalloc"})
    a = mod.aggregate(entries)
    v = mod.classify(True, False, a)
    assert v["verdict"] == "vmalloc_fragmentation"


# Priority: giant > fragmentation
def test_priority_giant_over_frag():
    entries = [{"size": 4096, "caller": f"f{i}",
                  "kind": "vmalloc"} for i in range(10_001)]
    entries[0] = {"size": 50 * 1024 * 1024, "caller": "leak",
                       "kind": "vmalloc"}
    a = mod.aggregate(entries)
    v = mod.classify(True, False, a)
    assert v["verdict"] == "vmalloc_giant_alloc"


# --- status integration -----------------------------------------

def test_status_unknown(tmp_path):
    out = mod.status(None, str(tmp_path / "nope"))
    assert out["ok"] is False
    assert out["verdict"]["verdict"] == "unknown"


def test_status_synthetic_ok(tmp_path):
    f = tmp_path / "vmallocinfo"
    f.write_text(
        "0xffff... 4096 f1+0x1/0x2 ioremap\n"
        "0xffff... 8192 f2+0x1/0x2 vmalloc\n"
        "0xffff... 16384 f3+0x1/0x2 vmap\n")
    out = mod.status(None, str(f))
    assert out["ok"] is True
    assert out["permission_denied"] is False
    assert out["alloc_count"] == 3
    assert out["total_bytes"] == 28672
    assert out["verdict"]["verdict"] == "ok"


def test_status_real_live():
    """The real /proc/vmallocinfo — should produce a sane
    verdict regardless of permissions."""
    out = mod.status(None)
    assert out["verdict"]["verdict"] in (
        "ok", "vmalloc_giant_alloc", "vmalloc_fragmentation",
        "requires_root", "unknown")
