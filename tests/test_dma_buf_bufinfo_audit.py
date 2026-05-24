"""Tests for modules/dma_buf_bufinfo_audit.py — R&D #91.2."""
from __future__ import annotations

import pytest

from gpu_dashboard.modules import dma_buf_bufinfo_audit as mod


_BUFINFO_SAMPLE = (
    "Buffer Objects:\n"
    "size       flags       mode     count   exp_name   "
    "ino       name\n"
    "0x00400000 00000002    rw-      1       drm     "
    "21        gbm-buffer\n"
    "0x00400000 00000002    rw-      1       drm     "
    "22        gbm-buffer\n"
    "0x00100000 00000002    rw-      1       i915    "
    "23        i915-bo\n"
    "Total: 3 buffers, 9216 KiB total\n")


def _mk_bufinfo(tmp_path, text=_BUFINFO_SAMPLE):
    p = tmp_path / "bufinfo"
    p.write_text(text)
    return str(p)


def _mk_meminfo(tmp_path, mem_kib=32 * 2**20):
    p = tmp_path / "meminfo"
    p.write_text(f"MemTotal:       {mem_kib} kB\n")
    return str(p)


# --- parse_meminfo_total_bytes ---------------------------------

def test_parse_meminfo_present():
    assert mod.parse_meminfo_total_bytes(
        "MemTotal: 32000000 kB\n") == 32000000 * 1024


def test_parse_meminfo_absent():
    assert mod.parse_meminfo_total_bytes("") is None


# --- parse_bufinfo ---------------------------------------------

def test_parse_bufinfo_empty():
    assert mod.parse_bufinfo("") == {}


def test_parse_bufinfo_skips_total_and_header():
    out = mod.parse_bufinfo(_BUFINFO_SAMPLE)
    assert "drm" in out
    assert "i915" in out
    # 2 drm rows of 0x00400000 = 0x00800000
    assert out["drm"] == 0x00800000


def test_parse_bufinfo_decimal_size():
    text = ("size flags mode count exp_name ino name\n"
            "4194304 00000002 rw- 1 drm 1 buf\n")
    out = mod.parse_bufinfo(text)
    assert out.get("drm") == 4194304


def test_parse_bufinfo_skips_garbage():
    text = ("Garbage banner\n"
            "0x00400000 02 rw- 1 drm 1 buf\n"
            "another garbage line\n")
    out = mod.parse_bufinfo(text)
    assert out.get("drm") == 0x00400000


# --- classify --------------------------------------------------

_MEM = 32 * 2**30  # 32 GiB


def test_classify_unknown_no_file():
    v = mod.classify(False, False, {}, _MEM)
    assert v["verdict"] == "unknown"


def test_classify_requires_root():
    v = mod.classify(True, False, {}, _MEM)
    assert v["verdict"] == "requires_root"


def test_classify_ok_no_buffers():
    v = mod.classify(True, True, {}, _MEM)
    assert v["verdict"] == "ok"


def test_classify_ok_small_footprint():
    v = mod.classify(True, True, {"drm": 100 * 2**20}, _MEM)
    assert v["verdict"] == "ok"


def test_classify_exporter_dominates():
    # > 50% of 32 GiB
    v = mod.classify(True, True,
                          {"drm": 20 * 2**30}, _MEM)
    assert v["verdict"] == "exporter_dominates"
    assert v["exporter"] == "drm"


def test_classify_top3_high():
    # 3 exporters each at 3 GiB = 9 GiB = ~28% of 32 GiB
    v = mod.classify(True, True, {
        "drm": 3 * 2**30,
        "i915": 3 * 2**30,
        "amdgpu": 3 * 2**30,
    }, _MEM)
    assert v["verdict"] == "top3_high_footprint"


def test_classify_dmabuf_footprint_high():
    # total > 10% but no single dominator, no top3 > 25%
    v = mod.classify(True, True, {
        "drm": 1500 * 2**20,
        "i915": 1500 * 2**20,
        "amdgpu": 800 * 2**20,
        "extra1": 100 * 2**20,
        "extra2": 100 * 2**20,
    }, _MEM)
    # 1500+1500+800+100+100 = 4000 MiB on 32 GiB → ~12%
    # but top3 = 1500+1500+800 = 3800 MiB ~ 11.6% < 25%
    assert v["verdict"] == "dmabuf_footprint_high"


# Priority : dominates > top3 > footprint > ok
def test_priority_dominates_over_top3():
    v = mod.classify(True, True, {
        "drm": 20 * 2**30,
        "i915": 3 * 2**30,
        "amdgpu": 3 * 2**30,
    }, _MEM)
    assert v["verdict"] == "exporter_dominates"


def test_priority_top3_over_footprint():
    v = mod.classify(True, True, {
        "drm": 3 * 2**30,
        "i915": 3 * 2**30,
        "amdgpu": 3 * 2**30,
        "extra": 1 * 2**30,
    }, _MEM)
    assert v["verdict"] == "top3_high_footprint"


# --- status integration ----------------------------------------

def test_status_unknown(tmp_path):
    out = mod.status(None,
                       str(tmp_path / "nope"),
                       str(tmp_path / "nope_mem"))
    assert out["verdict"]["verdict"] == "unknown"


def test_status_ok_synthetic(tmp_path):
    b = _mk_bufinfo(tmp_path)
    m = _mk_meminfo(tmp_path)
    out = mod.status(None, b, m)
    # 9 MiB on 32 GiB = ~0.03% → ok
    assert out["verdict"]["verdict"] == "ok"
    assert out["exporter_count"] >= 1


def test_status_dominates_synthetic(tmp_path):
    # Single drm row at 20 GiB
    text = (
        "Buffer Objects:\n"
        "size flags mode count exp_name ino name\n"
        f"0x{20 * 2**30:016x} 02 rw- 1 drm 1 huge\n")
    p = tmp_path / "bufinfo"
    p.write_text(text)
    m = _mk_meminfo(tmp_path)
    out = mod.status(None, str(p), m)
    assert out["verdict"]["verdict"] == "exporter_dominates"
