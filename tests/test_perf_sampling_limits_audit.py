"""Tests for modules/perf_sampling_limits_audit.py R&D #100.3."""
from __future__ import annotations

import pytest

from gpu_dashboard.modules import perf_sampling_limits_audit as mod


# --- classify --------------------------------------------------

def test_classify_unknown():
    v = mod.classify(False, None, None, None, None)
    assert v["verdict"] == "unknown"


def test_classify_requires_root():
    v = mod.classify(True, None, None, None, None)
    assert v["verdict"] == "requires_root"


def test_classify_ok():
    v = mod.classify(True, 50, 100000, 4096, 127)
    assert v["verdict"] == "ok"


def test_classify_throttle_floor_err():
    v = mod.classify(True, 25, 5000, 4096, 127)
    assert v["verdict"] == "perf_throttle_25pct_floor_hit"


def test_classify_throttle_needs_both():
    # cpu_time<=25 but sample_rate at default → not err
    v = mod.classify(True, 25, 100000, 4096, 127)
    assert v["verdict"] != "perf_throttle_25pct_floor_hit"


def test_classify_mlock_starved_warn():
    v = mod.classify(True, 50, 100000, 516, 127)
    assert v["verdict"] == "perf_mlock_kb_starved"


def test_classify_stack_shallow_accent():
    v = mod.classify(True, 50, 100000, 4096, 64)
    assert v["verdict"] == "perf_max_stack_shallow"


# Priority : throttle > mlock > stack
def test_priority_throttle_over_mlock():
    v = mod.classify(True, 25, 5000, 100, 127)
    assert v["verdict"] == "perf_throttle_25pct_floor_hit"


def test_priority_mlock_over_stack():
    v = mod.classify(True, 50, 100000, 100, 64)
    assert v["verdict"] == "perf_mlock_kb_starved"


# --- status integration ----------------------------------------

def test_status_unknown(tmp_path):
    out = mod.status(None, str(tmp_path / "nope"))
    assert out["verdict"]["verdict"] == "unknown"


def test_status_ok_synthetic(tmp_path):
    d = tmp_path / "kernel"
    d.mkdir()
    (d / "perf_cpu_time_max_percent").write_text("50\n")
    (d / "perf_event_max_sample_rate").write_text("100000\n")
    (d / "perf_event_mlock_kb").write_text("4096\n")
    (d / "perf_event_max_stack").write_text("127\n")
    out = mod.status(None, str(d))
    assert out["verdict"]["verdict"] == "ok"


def test_status_mlock_starved_synthetic(tmp_path):
    d = tmp_path / "kernel"
    d.mkdir()
    (d / "perf_cpu_time_max_percent").write_text("50\n")
    (d / "perf_event_max_sample_rate").write_text("100000\n")
    (d / "perf_event_mlock_kb").write_text("516\n")
    (d / "perf_event_max_stack").write_text("127\n")
    out = mod.status(None, str(d))
    assert (out["verdict"]["verdict"]
            == "perf_mlock_kb_starved")
    assert out["perf_event_mlock_kb"] == 516
