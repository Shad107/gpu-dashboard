"""Tests for /api/benchmark/run (R&D #4.2, cycle 123)."""
from unittest.mock import MagicMock

import pytest

from gpu_dashboard import api


class FakeSampler:
    def __init__(self):
        self.interval = 5
    def snapshot(self):
        return [
            {"ts_epoch": 1000 + i * 5, "power": 100.0, "temp": 55, "util_gpu": 50}
            for i in range(12)
        ]


def _ctx(monkeypatch):
    # Avoid hitting actual nvidia-smi
    monkeypatch.setattr(api, "handle_power_profile_apply",
                        lambda ctx, name: (200, {"ok": True, "name": name}))
    # Avoid real sleeping — replace _time.sleep at module level
    import gpu_dashboard.modules.benchmark as bm
    monkeypatch.setattr(bm._time, "sleep", lambda s: None)
    return {"sampler": FakeSampler(), "config": None}


def test_run_returns_comparison(monkeypatch):
    code, body = api.handle_benchmark_run(_ctx(monkeypatch), {
        "profile_a": "silent", "profile_b": "boost", "duration_s": 30,
    })
    assert code == 200
    assert body["segment_a"]["profile"] == "silent"
    assert body["segment_b"]["profile"] == "boost"
    assert "comparison" in body
    assert "winners" in body["comparison"]


def test_run_rejects_unknown_profile(monkeypatch):
    code, body = api.handle_benchmark_run(_ctx(monkeypatch), {
        "profile_a": "ultra", "profile_b": "boost", "duration_s": 30,
    })
    assert code == 400


def test_run_rejects_same_profile(monkeypatch):
    code, body = api.handle_benchmark_run(_ctx(monkeypatch), {
        "profile_a": "boost", "profile_b": "boost", "duration_s": 30,
    })
    assert code == 400
    assert "differ" in body["error"]


def test_run_rejects_short_duration(monkeypatch):
    code, body = api.handle_benchmark_run(_ctx(monkeypatch), {
        "profile_a": "silent", "profile_b": "boost", "duration_s": 1,
    })
    assert code == 400


def test_run_rejects_long_duration(monkeypatch):
    code, body = api.handle_benchmark_run(_ctx(monkeypatch), {
        "profile_a": "silent", "profile_b": "boost", "duration_s": 9999,
    })
    assert code == 400


def test_run_no_sampler_returns_503(monkeypatch):
    monkeypatch.setattr(api, "handle_power_profile_apply",
                        lambda ctx, name: (200, {"ok": True}))
    code, body = api.handle_benchmark_run({"sampler": None}, {
        "profile_a": "silent", "profile_b": "boost", "duration_s": 30,
    })
    assert code == 503


def test_run_rejects_non_dict():
    code, body = api.handle_benchmark_run({}, "not a dict")
    assert code == 400
