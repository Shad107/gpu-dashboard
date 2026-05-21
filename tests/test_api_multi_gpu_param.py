"""Tests for ?gpu_index= query param propagating through data endpoints (cycle 88)."""
import pytest

from gpu_dashboard import api
from gpu_dashboard.config import Config
from gpu_dashboard.storage import Storage


@pytest.fixture
def ctx(tmp_path):
    s = Storage(str(tmp_path / "metrics.db"))
    cfg = Config(defaults={"ELECTRICITY_PRICE_EUR_PER_KWH": "0.25"})
    # GPU 0 hot, GPU 1 cool — different temps so we can verify filtering
    for ts in range(1000, 1010):
        s.record_sample({"ts": ts, "temp": 70, "power": 200, "gpu_index": 0})
        s.record_sample({"ts": ts, "temp": 40, "power": 50,  "gpu_index": 1})
    yield {"storage": s, "config": cfg}
    s.close()


def test_history_gpu_0(ctx):
    code, body = api.handle_history(ctx, {"from": "0"})
    assert code == 200
    assert all(r["gpu_index"] == 0 for r in body["samples"])
    assert all(r["temp"] == 70 for r in body["samples"])
    assert body["gpu_index"] == 0


def test_history_gpu_1(ctx):
    code, body = api.handle_history(ctx, {"from": "0", "gpu_index": "1"})
    assert all(r["gpu_index"] == 1 for r in body["samples"])
    assert all(r["temp"] == 40 for r in body["samples"])
    assert body["gpu_index"] == 1


def test_thermal_stats_accepts_param(ctx):
    _, body = api.handle_thermal_stats(ctx, {"gpu_index": "1"})
    # samples are at ts=1000-1010 (long ago vs now), so 24h window has nothing
    # but the call shouldn't error and shape is correct
    assert "series_24h" in body


def test_power_stats_accepts_param(ctx):
    _, body = api.handle_power_stats(ctx, {"gpu_index": "1"})
    assert "series_24h" in body


def test_invalid_gpu_index_falls_back_to_zero(ctx):
    code, body = api.handle_history(ctx, {"from": "0", "gpu_index": "abc"})
    assert code == 200
    assert body["gpu_index"] == 0


def test_parse_gpu_index_helper():
    assert api._parse_gpu_index({}) == 0
    assert api._parse_gpu_index({"gpu_index": "5"}) == 5
    assert api._parse_gpu_index({"gpu_index": "abc"}) == 0
    assert api._parse_gpu_index({"gpu_index": ""}) == 0
