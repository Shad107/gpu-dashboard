"""Tests for modules/clk_summary_audit.py — R&D #83.2."""
from __future__ import annotations

import pytest

from gpu_dashboard.modules import clk_summary_audit as mod


SAMPLE_HEADER = (
    "                                 enable  prepare  "
    "protect                                duty  hardware"
    "                            connection\n"
    "   clock                          count    count    "
    "count        rate   accuracy phase  cycle    enable"
    "   consumer                         id\n"
    "----------------------------------------------------"
    "-----------------------------------------------------"
    "------------------------------------\n")


def _clock_row(name, depth=0, enable=1, prepare=1, protect=0,
                consumer="someconsumer", id_="someid"):
    indent = " " * depth
    return (f"{indent}{name:30s} {enable:8d} {prepare:8d} "
            f"{protect:8d} 24000000 0 0 50000 Y {consumer:30s} "
            f"{id_}\n")


# --- parse_summary ---------------------------------------------

def test_parse_empty():
    assert mod.parse_summary("") == []


def test_parse_header_only():
    assert mod.parse_summary(SAMPLE_HEADER) == []


def test_parse_one_clock():
    text = SAMPLE_HEADER + _clock_row("xtal_24m")
    out = mod.parse_summary(text)
    assert len(out) == 1
    assert out[0]["name"] == "xtal_24m"
    assert out[0]["enable"] == 1
    assert out[0]["prepare"] == 1


def test_parse_indented_child():
    text = (SAMPLE_HEADER
              + _clock_row("xtal_24m", depth=0)
              + _clock_row("pll_main", depth=3))
    out = mod.parse_summary(text)
    assert len(out) == 2
    assert out[0]["depth"] == 0
    assert out[1]["depth"] == 3


def test_parse_skips_garbage_line():
    text = SAMPLE_HEADER + "bad line\n" + _clock_row("x")
    out = mod.parse_summary(text)
    assert len(out) == 1


# --- read_summary ----------------------------------------------

def test_read_summary_unknown(tmp_path):
    text, state = mod.read_summary(str(tmp_path / "nope"))
    assert text is None
    assert state == "unknown"


def test_read_summary_present(tmp_path):
    d = tmp_path / "clk"
    d.mkdir()
    (d / "clk_summary").write_text(SAMPLE_HEADER)
    text, state = mod.read_summary(str(d))
    assert text is not None
    assert state == "ok"


# --- classify --------------------------------------------------

def test_classify_unknown_no_dir():
    v = mod.classify(None, "unknown")
    assert v["verdict"] == "unknown"


def test_classify_requires_root():
    v = mod.classify(None, "requires_root")
    assert v["verdict"] == "requires_root"


def test_classify_unknown_empty_parse():
    v = mod.classify([], "ok")
    assert v["verdict"] == "unknown"


def _clk(name="x", depth=0, enable=1, prepare=1, protect=0,
          consumer="ok_consumer"):
    return {"name": name, "depth": depth, "enable": enable,
              "prepare": prepare, "protect": protect,
              "consumer": consumer}


def test_classify_ok_consistent():
    v = mod.classify([
        _clk("xtal_24m", depth=0),
        _clk("pll_main", depth=3),
        _clk("cpu_clk", depth=6, enable=4, prepare=4),
    ], "ok")
    assert v["verdict"] == "ok"


def test_classify_orphan_clock_enabled():
    v = mod.classify([
        _clk("xtal_24m", depth=0, consumer="ok"),
        _clk("dead_pll", depth=0, enable=2,
              consumer="deviceless"),
    ], "ok")
    assert v["verdict"] == "orphan_clock_enabled"
    assert v["first_orphan"] == "dead_pll"


def test_classify_orphan_via_no_consumer():
    v = mod.classify([
        _clk("dead_pll", enable=1,
              consumer="no_consumer"),
    ], "ok")
    assert v["verdict"] == "orphan_clock_enabled"


def test_classify_orphan_disabled_ok():
    # enable=0 with no consumer is fine — not running
    v = mod.classify([
        _clk("x", enable=0, consumer="deviceless"),
    ], "ok")
    assert v["verdict"] == "ok"


def test_classify_prepare_enable_drift():
    v = mod.classify([
        _clk("x", enable=1, prepare=5, consumer="ok"),
    ], "ok")
    assert v["verdict"] == "prepare_enable_drift"


def test_classify_minor_drift_ok():
    # prepare - enable = 1 is allowed (off-by-one is normal)
    v = mod.classify([
        _clk("x", enable=1, prepare=2, consumer="ok"),
    ], "ok")
    assert v["verdict"] == "ok"


# Priority : orphan > unused > drift
def test_priority_orphan_over_drift():
    v = mod.classify([
        _clk("a", enable=2, consumer="deviceless"),
        _clk("b", enable=1, prepare=10, consumer="ok"),
    ], "ok")
    assert v["verdict"] == "orphan_clock_enabled"


# --- status integration ----------------------------------------

def test_status_unknown(tmp_path):
    out = mod.status(None, str(tmp_path / "nope"))
    assert out["verdict"]["verdict"] == "unknown"


def test_status_requires_root_synthetic(tmp_path):
    # debug/clk exists but clk_summary is unreadable
    d = tmp_path / "clk"
    d.mkdir()
    # Don't create clk_summary at all
    out = mod.status(None, str(d))
    assert out["read_state"] == "requires_root"
    assert out["verdict"]["verdict"] == "requires_root"


def test_status_ok_synthetic(tmp_path):
    d = tmp_path / "clk"
    d.mkdir()
    (d / "clk_summary").write_text(
        SAMPLE_HEADER + _clock_row("xtal_24m"))
    out = mod.status(None, str(d))
    assert out["read_state"] == "ok"
    assert out["clock_count"] == 1
    assert out["verdict"]["verdict"] == "ok"


def test_status_orphan_synthetic(tmp_path):
    d = tmp_path / "clk"
    d.mkdir()
    (d / "clk_summary").write_text(
        SAMPLE_HEADER
        + _clock_row("dead", consumer="deviceless"))
    out = mod.status(None, str(d))
    assert out["ok"] is False
    assert (out["verdict"]["verdict"]
            == "orphan_clock_enabled")
