"""Tests for modules/khugepaged_pressure_audit.py R&D #92.3."""
from __future__ import annotations

import json
import os

import pytest

from gpu_dashboard.modules import (
    khugepaged_pressure_audit as mod)


_VMSTAT_BASE = (
    "thp_collapse_alloc 100\n"
    "thp_collapse_alloc_failed 5\n"
    "thp_fault_fallback 0\n"
    "thp_split_page 10\n"
)


def _mk_vmstat(tmp_path, text=_VMSTAT_BASE, name="vmstat"):
    p = tmp_path / name
    p.write_text(text)
    return str(p)


def _mk_khugepaged(tmp_path, *, present=True,
                    pages_collapsed="100",
                    max_ptes_none="511",
                    scan_sleep_ms="10000"):
    d = tmp_path / "khugepaged"
    if not present:
        return str(d)
    d.mkdir(parents=True, exist_ok=True)
    (d / "pages_collapsed").write_text(
        pages_collapsed + "\n")
    (d / "max_ptes_none").write_text(max_ptes_none + "\n")
    (d / "scan_sleep_millisecs").write_text(
        scan_sleep_ms + "\n")
    (d / "alloc_sleep_millisecs").write_text("60000\n")
    return str(d)


# --- parse_vmstat_thp ------------------------------------------

def test_parse_vmstat_empty():
    assert mod.parse_vmstat_thp("") == {}


def test_parse_vmstat_typical():
    out = mod.parse_vmstat_thp(_VMSTAT_BASE)
    assert out["thp_collapse_alloc"] == 100
    assert out["thp_collapse_alloc_failed"] == 5


def test_parse_vmstat_ignores_non_thp():
    text = "thp_collapse_alloc 5\noom_kill 0\n"
    out = mod.parse_vmstat_thp(text)
    assert "thp_collapse_alloc" in out
    assert "oom_kill" not in out


# --- load_prev / save_state ------------------------------------

def test_load_prev_missing(tmp_path):
    assert mod.load_prev(str(tmp_path / "nope")) is None


def test_load_prev_corrupt(tmp_path):
    p = tmp_path / "state.json"
    p.write_text("not json")
    assert mod.load_prev(str(p)) is None


def test_load_prev_valid(tmp_path):
    p = tmp_path / "state.json"
    p.write_text('{"counters":{"thp_collapse_alloc":50}}')
    out = mod.load_prev(str(p))
    assert out["counters"]["thp_collapse_alloc"] == 50


# --- compute_deltas --------------------------------------------

def test_compute_deltas_no_prev():
    out = mod.compute_deltas(
        {"thp_collapse_alloc": 10}, None)
    assert out["thp_collapse_alloc"] == 10


def test_compute_deltas_with_prev():
    out = mod.compute_deltas(
        {"thp_collapse_alloc": 10,
         "thp_collapse_alloc_failed": 5},
        {"counters": {"thp_collapse_alloc": 3,
                      "thp_collapse_alloc_failed": 2}})
    assert out["thp_collapse_alloc"] == 7
    assert out["thp_collapse_alloc_failed"] == 3


# --- classify --------------------------------------------------

def _zero_deltas():
    return {k: 0 for k in mod._COUNTERS}


def test_classify_unknown_no_khugepaged():
    v = mod.classify(_zero_deltas(), True, False)
    assert v["verdict"] == "unknown"


def test_classify_unknown_no_prev():
    v = mod.classify(_zero_deltas(), False, True)
    assert v["verdict"] == "unknown"


def test_classify_ok_idle():
    v = mod.classify(_zero_deltas(), True, True)
    assert v["verdict"] == "ok"


def test_classify_ok_low_activity_high_fail():
    # 20 attempts total, 90% failed — below _MIN_ACTIVITY=50
    d = _zero_deltas()
    d["thp_collapse_alloc"] = 2
    d["thp_collapse_alloc_failed"] = 18
    v = mod.classify(d, True, True)
    assert v["verdict"] == "ok"


def test_classify_ok_healthy_high_activity():
    d = _zero_deltas()
    d["thp_collapse_alloc"] = 100
    d["thp_collapse_alloc_failed"] = 10
    v = mod.classify(d, True, True)
    assert v["verdict"] == "ok"


def test_classify_collapse_failing_hot():
    # 100 total, failure > 2x successes
    d = _zero_deltas()
    d["thp_collapse_alloc"] = 20
    d["thp_collapse_alloc_failed"] = 80
    v = mod.classify(d, True, True)
    assert v["verdict"] == "collapse_failing_hot"
    assert v["failed"] == 80
    assert v["succeeded"] == 20


# --- status integration ----------------------------------------

def test_status_unknown_no_khugepaged(tmp_path):
    v = _mk_vmstat(tmp_path)
    k = _mk_khugepaged(tmp_path, present=False)
    sp = str(tmp_path / "state.json")
    out = mod.status(None, v, k, sp)
    assert out["verdict"]["verdict"] == "unknown"
    assert out["khugepaged_present"] is False


def test_status_first_run_unknown(tmp_path):
    v = _mk_vmstat(tmp_path)
    k = _mk_khugepaged(tmp_path)
    sp = str(tmp_path / "state.json")
    out = mod.status(None, v, k, sp)
    assert out["verdict"]["verdict"] == "unknown"
    assert os.path.isfile(sp)  # state saved


def test_status_second_run_ok_no_change(tmp_path):
    v = _mk_vmstat(tmp_path)
    k = _mk_khugepaged(tmp_path)
    sp = str(tmp_path / "state.json")
    mod.status(None, v, k, sp)  # baseline
    out = mod.status(None, v, k, sp)
    assert out["verdict"]["verdict"] == "ok"


def test_status_failure_storm_synthetic(tmp_path):
    k = _mk_khugepaged(tmp_path)
    sp = str(tmp_path / "state.json")
    # First run baseline
    v1 = _mk_vmstat(tmp_path,
                          _VMSTAT_BASE, name="v1")
    mod.status(None, v1, k, sp)
    # Second run with bumped failures
    bumped = (
        "thp_collapse_alloc 110\n"
        "thp_collapse_alloc_failed 105\n"
        "thp_fault_fallback 0\n"
        "thp_split_page 10\n")
    v2 = _mk_vmstat(tmp_path, bumped, name="v2")
    out = mod.status(None, v2, k, sp)
    assert (out["verdict"]["verdict"]
            == "collapse_failing_hot")
