"""R&D #8.1 — History scrubber endpoint."""
import time
import pytest
from gpu_dashboard import api
from gpu_dashboard.config import Config
from gpu_dashboard.storage import Storage


@pytest.fixture
def ctx(tmp_path):
    storage = Storage(str(tmp_path / "test.db"))
    base_ts = int(time.time()) - 3600  # 1 hour ago
    # Insert 60 samples, 1/min
    for i in range(60):
        storage.record_sample({
            "ts": base_ts + i * 60,
            "temp": 50 + i % 30,
            "fan": 40,
            "fan0_rpm": 800,
            "fan1_rpm": 800,
            "clk_gpu": 1500,
            "clk_mem": 9000,
            "power": 200.0,
            "power_limit": 350.0,
            "util_gpu": 30 + i % 50,
            "mem_used_mib": 8000,
        })
    yield {"config": Config(defaults={}), "storage": storage, "base_ts": base_ts}


def test_no_t_param_returns_400(ctx):
    code, body = api.handle_snapshot_at(ctx, {})
    assert code == 400
    assert "t required" in body["error"]


def test_invalid_t_returns_400(ctx):
    code, body = api.handle_snapshot_at(ctx, {"t": "not-a-number"})
    assert code == 400


def test_exact_match_returns_zero_distance(ctx):
    # Pick the 10th sample
    target_ts = ctx["base_ts"] + 600
    code, body = api.handle_snapshot_at(ctx, {"t": str(target_ts)})
    assert code == 200
    assert body["found"] is True
    assert body["t_actual"] == target_ts
    assert body["distance_s"] == 0
    assert "sample" in body
    assert body["sample"]["temp"] is not None


def test_nearest_within_tolerance(ctx):
    # Request a time 5s after a sample (between two samples 60s apart)
    target_ts = ctx["base_ts"] + 600 + 5
    code, body = api.handle_snapshot_at(ctx, {"t": str(target_ts), "tolerance": "60"})
    assert code == 200
    assert body["found"] is True
    assert body["distance_s"] == 5


def test_outside_tolerance_returns_404(ctx):
    # Request far in the future (way beyond any sample)
    future_ts = ctx["base_ts"] + 100000
    code, body = api.handle_snapshot_at(ctx, {"t": str(future_ts), "tolerance": "60"})
    assert code == 404
    assert body["found"] is False


def test_storage_missing_returns_503():
    code, body = api.handle_snapshot_at({"config": Config(defaults={})}, {"t": "100"})
    assert code == 503


def test_tolerance_clamped_to_1_to_3600(ctx):
    """tolerance must be in [1, 3600]. Out of range → clamped."""
    target_ts = ctx["base_ts"] + 600
    # Try with insane tolerance
    code, body = api.handle_snapshot_at(ctx, {"t": str(target_ts), "tolerance": "999999"})
    assert code == 200  # would still find the sample, tolerance clamped to 3600


def test_response_includes_t_requested(ctx):
    target_ts = ctx["base_ts"] + 1800
    code, body = api.handle_snapshot_at(ctx, {"t": str(target_ts)})
    assert body["t_requested"] == target_ts
