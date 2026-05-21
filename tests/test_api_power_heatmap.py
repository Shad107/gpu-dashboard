"""Tests for /api/power-heatmap — 24-bucket power+cost by hour-of-day."""
from __future__ import annotations

import datetime as _dt
import time

import pytest

from gpu_dashboard import api
from gpu_dashboard.config import Config
from gpu_dashboard.storage import Storage


@pytest.fixture
def ctx(tmp_path):
    storage = Storage(str(tmp_path / "metrics.db"))
    cfg = Config(defaults={
        "ELECTRICITY_PRICE_EUR_PER_KWH": "0.25",
        "ELECTRICITY_CURRENCY": "EUR",
    })
    yield {"storage": storage, "config": cfg}
    storage.close()


def _ts_at_hour(days_ago: int, hour: int) -> int:
    """Build a UTC-ish epoch for `hour`:00 `days_ago` days back."""
    d = _dt.datetime.now() - _dt.timedelta(days=days_ago)
    d = d.replace(hour=hour, minute=0, second=0, microsecond=0)
    return int(d.timestamp())


class TestPowerHeatmap:
    def test_no_storage_returns_503(self):
        code, body = api.handle_power_heatmap({}, {})
        assert code == 503

    def test_empty_returns_24_zero_cells(self, ctx):
        code, body = api.handle_power_heatmap(ctx, {})
        assert code == 200
        assert len(body["hours"]) == 24
        for cell in body["hours"]:
            assert cell["avg_watts"] == 0
            assert cell["sample_count"] == 0

    def test_samples_in_one_hour_only(self, ctx):
        # Plant 3 samples at hour=14 yesterday
        for offset in range(3):
            ctx["storage"].record_sample({
                "ts": _ts_at_hour(1, 14) + offset * 60,
                "power": 200.0, "temp": 50,
            })
        code, body = api.handle_power_heatmap(ctx, {})
        # hour 14 should have data
        cell14 = next(c for c in body["hours"] if c["hour"] == 14)
        assert cell14["sample_count"] == 3
        assert cell14["avg_watts"] == pytest.approx(200.0, abs=0.1)
        # Other hours should be 0
        for cell in body["hours"]:
            if cell["hour"] != 14:
                assert cell["sample_count"] == 0

    def test_cost_uses_configured_rate(self, ctx):
        ctx["storage"].record_sample({"ts": _ts_at_hour(1, 10), "power": 1000.0, "temp": 50})
        _, body = api.handle_power_heatmap(ctx, {})
        cell10 = next(c for c in body["hours"] if c["hour"] == 10)
        # 1000W avg → 1 kWh/h → 0.25 €/h
        assert cell10["cost_per_hour"] == pytest.approx(0.25, abs=0.001)

    def test_days_param_filters(self, ctx):
        # Sample 10 days ago (outside default 7-day window)
        ctx["storage"].record_sample({"ts": _ts_at_hour(10, 5), "power": 999.0, "temp": 50})
        _, body = api.handle_power_heatmap(ctx, {"days": "7"})
        cell5 = next(c for c in body["hours"] if c["hour"] == 5)
        assert cell5["sample_count"] == 0  # outside window

    def test_returns_metadata(self, ctx):
        _, body = api.handle_power_heatmap(ctx, {})
        assert body["days"] == 7  # default
        assert body["currency"] == "EUR"
        assert body["price_per_kwh"] == 0.25

    def test_invalid_days_returns_400(self, ctx):
        code, body = api.handle_power_heatmap(ctx, {"days": "abc"})
        assert code == 400
