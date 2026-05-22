"""Tests for the benchmark helpers (R&D #4, cycle 122)."""
import pytest

from gpu_dashboard.modules.benchmark import run_segment, compare


class FakeSampler:
    """Returns a fixed list of samples. interval used by fallback."""
    def __init__(self, samples, interval=5):
        self._samples = samples
        self.interval = interval
    def snapshot(self):
        return list(self._samples)


def _silent_samples(n=12, base_ts=1000):
    """Idle-ish samples : 50 W, 45°C, 5% util, no token activity."""
    return [
        {"ts_epoch": base_ts + i * 5, "power": 50.0, "temp": 45, "util_gpu": 5,
         "tokens_total_snapshot": 1000}
        for i in range(n)
    ]


def _boost_samples(n=12, base_ts=2000):
    """Boost samples : 300 W, 75°C, 95% util, token throughput climbing."""
    return [
        {"ts_epoch": base_ts + i * 5, "power": 300.0, "temp": 75, "util_gpu": 95,
         "tokens_total_snapshot": 1000 + i * 800}
        for i in range(n)
    ]


def _no_op_apply(name):
    pass


def test_run_segment_returns_aggregates():
    sampler = FakeSampler(_silent_samples(base_ts=1000))
    seg = run_segment(60, "silent", _no_op_apply, sampler,
                      sleep=lambda s: None, now=lambda: 1060)
    assert seg["profile"] == "silent"
    assert seg["avg_power_w"] == 50.0
    assert seg["avg_temp_c"] == 45.0
    assert seg["samples_count"] == 12
    # kWh = 50W × 60s / 3600 / 1000 = 0.000833
    assert 0.0008 <= seg["kwh"] <= 0.001


def test_run_segment_records_apply_error():
    def failing_apply(name): raise RuntimeError("boom")
    sampler = FakeSampler(_silent_samples())
    seg = run_segment(60, "silent", failing_apply, sampler,
                      sleep=lambda s: None, now=lambda: 1060)
    assert seg["apply_error"] == "boom"
    # But aggregates still computed
    assert seg["avg_power_w"] == 50.0


def test_run_segment_no_samples_in_window_uses_tail():
    """If sampler returns samples but none have ts_epoch in [start,end],
    fall back to tail samples."""
    # Samples have ts_epoch but in a different time range
    sampler = FakeSampler([
        {"ts_epoch": 9999, "power": 100, "temp": 50, "util_gpu": 10}
    ] * 5)
    seg = run_segment(60, "silent", _no_op_apply, sampler,
                      sleep=lambda s: None, now=lambda: 1060)
    # Fallback path : tail samples are used → avg_power_w from those samples
    assert seg["avg_power_w"] == 100.0


def test_run_segment_tokens_delta_and_throughput():
    """When boost samples show climbing tokens_total_snapshot, tokens_delta
    and tokens_per_s should be computed."""
    sampler = FakeSampler(_boost_samples(n=12, base_ts=2000))
    seg = run_segment(60, "boost", _no_op_apply, sampler,
                      sleep=lambda s: None, now=lambda: 2060)
    # 11 deltas of 800 = 8800
    assert seg["tokens_delta"] == 11 * 800
    # tokens_per_s = 8800 / 60 ≈ 146.7
    assert 145 < seg["tokens_per_s"] < 150


def test_compare_reports_winners():
    silent = run_segment(60, "silent", _no_op_apply,
                        FakeSampler(_silent_samples(base_ts=1000)),
                        sleep=lambda s: None, now=lambda: 1060)
    boost = run_segment(60, "boost", _no_op_apply,
                       FakeSampler(_boost_samples(base_ts=2000)),
                       sleep=lambda s: None, now=lambda: 2060)
    cmp = compare(silent, boost)
    assert cmp["profile_a"] == "silent"
    assert cmp["profile_b"] == "boost"
    # Boost is hotter, higher power, higher throughput, more efficient (since tokens climb)
    assert cmp["winners"]["cooler"] == "silent"
    assert cmp["winners"]["lower_power"] == "silent"
    assert cmp["winners"]["higher_throughput"] == "boost"
    assert cmp["winners"]["more_efficient"] == "boost"  # silent had 0 tokens/kWh
    assert cmp["winners"]["cheaper"] == "silent"  # boost burns 6× more energy


def test_compare_tie_when_identical():
    seg = run_segment(60, "silent", _no_op_apply,
                     FakeSampler(_silent_samples(base_ts=1000)),
                     sleep=lambda s: None, now=lambda: 1060)
    cmp = compare(seg, dict(seg, profile="silent2"))
    # All identical metrics → tie everywhere
    for w in cmp["winners"].values():
        assert w == "tie"


def test_compare_delta_signs():
    """delta should be B - A (positive = B higher)."""
    a = {"profile": "a", "avg_power_w": 100, "peak_power_w": 110,
         "avg_temp_c": 50, "avg_util_gpu": 30, "tokens_delta": 0,
         "tokens_per_s": 0, "tokens_per_kwh": 0, "kwh": 0.5, "cost": 0.125}
    b = {"profile": "b", "avg_power_w": 250, "peak_power_w": 280,
         "avg_temp_c": 70, "avg_util_gpu": 90, "tokens_delta": 10000,
         "tokens_per_s": 167, "tokens_per_kwh": 80000, "kwh": 0.125, "cost": 0.031}
    cmp = compare(a, b)
    assert cmp["delta"]["avg_power_w"] == 150
    assert cmp["delta"]["avg_temp_c"] == 20
    assert cmp["delta"]["tokens_per_s"] == 167
    # B's cost is lower → negative delta
    assert cmp["delta"]["cost"] < 0
