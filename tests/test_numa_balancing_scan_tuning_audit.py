"""Tests for modules/numa_balancing_scan_tuning_audit.py R&D #107.4."""
from __future__ import annotations

import pytest

from gpu_dashboard.modules import numa_balancing_scan_tuning_audit as mod


def _knobs(*, delay=1000, period_min=1000,
           period_max=60000, scan_size=256):
    return {"scan_delay_ms": delay,
            "scan_period_min_ms": period_min,
            "scan_period_max_ms": period_max,
            "scan_size_mb": scan_size}


def test_classify_unknown_balancing_off():
    v = mod.classify(0, _knobs())
    assert v["verdict"] == "unknown"


def test_classify_unknown_balancing_none():
    v = mod.classify(None, _knobs())
    assert v["verdict"] == "unknown"


def test_classify_requires_root():
    v = mod.classify(
        1,
        {"scan_delay_ms": None,
         "scan_period_min_ms": None,
         "scan_period_max_ms": None,
         "scan_size_mb": None})
    assert v["verdict"] == "requires_root"


def test_classify_ok_defaults():
    v = mod.classify(1, _knobs())
    assert v["verdict"] == "ok"


def test_classify_aggressive_warn():
    v = mod.classify(1, _knobs(period_min=200))
    assert v["verdict"] == "aggressive_scan"


def test_classify_lethargic_warn():
    v = mod.classify(1, _knobs(period_max=120000))
    assert v["verdict"] == "lethargic_scan"


def test_classify_tiny_chunk_accent():
    v = mod.classify(1, _knobs(scan_size=16))
    assert v["verdict"] == "tiny_scan_chunk"


def test_classify_drifted_accent():
    # 2 knobs differ from defaults but not by enough to fire
    # other verdicts
    v = mod.classify(1, _knobs(delay=2000, period_min=800))
    assert v["verdict"] == "drifted_from_defaults"


# Priority : aggressive > lethargic > tiny > drifted
def test_priority_aggressive_over_lethargic():
    v = mod.classify(1, _knobs(period_min=200,
                                    period_max=120000))
    assert v["verdict"] == "aggressive_scan"


def test_priority_tiny_over_drifted():
    v = mod.classify(1, _knobs(delay=2000, scan_size=16))
    assert v["verdict"] == "tiny_scan_chunk"


def test_status_unknown_no_balancing(tmp_path):
    d = tmp_path / "kernel"
    d.mkdir()
    (d / "numa_balancing").write_text("0\n")
    out = mod.status(None, str(d))
    assert out["verdict"]["verdict"] == "unknown"


def test_status_ok_synthetic(tmp_path):
    d = tmp_path / "kernel"
    d.mkdir()
    (d / "numa_balancing").write_text("1\n")
    (d / "numa_balancing_scan_delay_ms").write_text("1000\n")
    (d / "numa_balancing_scan_period_min_ms").write_text("1000\n")
    (d / "numa_balancing_scan_period_max_ms").write_text("60000\n")
    (d / "numa_balancing_scan_size_mb").write_text("256\n")
    out = mod.status(None, str(d))
    assert out["verdict"]["verdict"] == "ok"


def test_status_aggressive(tmp_path):
    d = tmp_path / "kernel"
    d.mkdir()
    (d / "numa_balancing").write_text("1\n")
    (d / "numa_balancing_scan_delay_ms").write_text("1000\n")
    (d / "numa_balancing_scan_period_min_ms").write_text("100\n")
    (d / "numa_balancing_scan_period_max_ms").write_text("60000\n")
    (d / "numa_balancing_scan_size_mb").write_text("256\n")
    out = mod.status(None, str(d))
    assert out["verdict"]["verdict"] == "aggressive_scan"
