"""Tests for modules/tracing_buffer_footprint_audit.py
R&D #95.3."""
from __future__ import annotations

import pytest

from gpu_dashboard.modules import (
    tracing_buffer_footprint_audit as mod)


def _mk_tracing(tmp_path, *, buffer_size_kb="7000",
                  buffer_total_size_kb="56000",
                  trace_clock="[global] local counter",
                  tracing_on="1",
                  per_cpu_stats=None,
                  files_unreadable=False):
    """per_cpu_stats: dict {cpu: overrun_count} or None."""
    d = tmp_path / "tracing"
    d.mkdir(parents=True, exist_ok=True)
    if not files_unreadable:
        if buffer_size_kb is not None:
            (d / "buffer_size_kb").write_text(
                buffer_size_kb + "\n")
        if buffer_total_size_kb is not None:
            (d / "buffer_total_size_kb").write_text(
                buffer_total_size_kb + "\n")
        if trace_clock is not None:
            (d / "trace_clock").write_text(trace_clock + "\n")
        if tracing_on is not None:
            (d / "tracing_on").write_text(tracing_on + "\n")
    if per_cpu_stats is not None:
        pc = d / "per_cpu"
        pc.mkdir(exist_ok=True)
        for cpu, overrun in per_cpu_stats.items():
            cd = pc / f"cpu{cpu}"
            cd.mkdir(exist_ok=True)
            (cd / "stats").write_text(
                f"entries: 0\noverrun: {overrun}\n")
    return str(d)


# --- parse_selected_clock --------------------------------------

def test_parse_clock_typical():
    assert mod.parse_selected_clock(
        "[local] global counter uptime") == "local"


def test_parse_clock_no_brackets():
    assert mod.parse_selected_clock("local global") == ""


# --- parse_per_cpu_overrun -------------------------------------

def test_parse_overrun_present():
    text = "entries: 100\noverrun: 50\ncommit: 100\n"
    assert mod.parse_per_cpu_overrun(text) == 50


def test_parse_overrun_absent():
    assert mod.parse_per_cpu_overrun("entries: 0\n") == 0


# --- read_state aggregates per-CPU overrun ---------------------

def test_read_state_aggregates_overrun(tmp_path):
    r = _mk_tracing(tmp_path, per_cpu_stats={
        0: 100, 1: 50, 2: 0})
    state = mod.read_state(r)
    assert state["total_overrun"] == 150


# --- classify --------------------------------------------------

def _state(**overrides):
    base = {
        "buffer_size_kb": 7000,
        "buffer_total_size_kb": 56000,
        "trace_clock_raw": "[global] local counter",
        "tracing_on": 1,
        "any_unreadable": False,
        "total_overrun": 0,
    }
    base.update(overrides)
    return base


def test_classify_unknown_no_root():
    v = mod.classify(_state(), 1, False)
    assert v["verdict"] == "unknown"


def test_classify_requires_root():
    v = mod.classify(_state(any_unreadable=True), 1, True)
    assert v["verdict"] == "requires_root"


def test_classify_overrun_active_err():
    v = mod.classify(
        _state(tracing_on=1, total_overrun=100),
        1, True)
    assert v["verdict"] == "buffer_overrun_active"


def test_classify_overrun_with_tracing_off_is_ok():
    v = mod.classify(
        _state(tracing_on=0, total_overrun=100), 1, True)
    # tracing_on=0 means we're not losing now
    assert v["verdict"] == "trace_buffer_sane"


def test_classify_buffer_over_512mb_warn():
    v = mod.classify(
        _state(buffer_total_size_kb=600 * 1024),
        4, True)
    assert v["verdict"] == "buffer_total_over_512mb"


def test_classify_trace_clock_local_smp_accent():
    v = mod.classify(
        _state(trace_clock_raw="[local] global"),
        4, True)
    assert v["verdict"] == "trace_clock_local_with_smp"


def test_classify_local_clock_uniprocessor_is_ok():
    v = mod.classify(
        _state(trace_clock_raw="[local] global"),
        1, True)
    assert v["verdict"] == "trace_buffer_sane"


def test_classify_ok():
    v = mod.classify(_state(), 4, True)
    assert v["verdict"] == "trace_buffer_sane"


# Priority : overrun > footprint > clock
def test_priority_overrun_over_footprint():
    v = mod.classify(
        _state(tracing_on=1, total_overrun=10,
               buffer_total_size_kb=600 * 1024),
        4, True)
    assert v["verdict"] == "buffer_overrun_active"


def test_priority_footprint_over_clock():
    v = mod.classify(
        _state(buffer_total_size_kb=600 * 1024,
               trace_clock_raw="[local] global"),
        4, True)
    assert v["verdict"] == "buffer_total_over_512mb"


# --- status integration ----------------------------------------

def test_status_unknown(tmp_path):
    out = mod.status(None, str(tmp_path / "nope"))
    assert out["verdict"]["verdict"] == "unknown"


def test_status_ok_synthetic(tmp_path):
    r = _mk_tracing(tmp_path, per_cpu_stats={0: 0, 1: 0})
    out = mod.status(None, r)
    assert out["verdict"]["verdict"] == "trace_buffer_sane"
    assert out["trace_clock"] == "global"


def test_status_overrun_synthetic(tmp_path):
    r = _mk_tracing(tmp_path,
                       per_cpu_stats={0: 500, 1: 0})
    out = mod.status(None, r)
    assert out["verdict"]["verdict"] == "buffer_overrun_active"
    assert out["total_overrun"] == 500
