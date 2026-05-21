"""Tests for /api/electricity — kWh and €/month cost estimation."""
from __future__ import annotations

import time

import pytest

from gpu_dashboard import api
from gpu_dashboard.config import Config
from gpu_dashboard.storage import Storage


@pytest.fixture
def ctx_with_samples(tmp_path):
    storage = Storage(str(tmp_path / "metrics.db"))
    # 60 samples over 1 hour with average 250W power draw
    # → energy = 250W × 1h = 250 Wh = 0.25 kWh
    now = int(time.time())
    for i in range(60):
        storage.record_sample({
            "ts": now - (3600 - i * 60),
            "power": 250.0,
            "temp": 50,
            "fan_pct": 40,
        })
    cfg = Config(defaults={
        "ELECTRICITY_PRICE_EUR_PER_KWH": "0.25",
        "ELECTRICITY_CURRENCY": "EUR",
    })
    yield {"storage": storage, "config": cfg}
    storage.close()


class TestHandleElectricity:
    def test_returns_avg_power(self, ctx_with_samples):
        code, body = api.handle_electricity(ctx_with_samples, {})
        assert code == 200
        # avg should be ~250W
        assert 240 < body["avg_power_watts"] < 260

    def test_returns_kwh(self, ctx_with_samples):
        code, body = api.handle_electricity(ctx_with_samples, {})
        # ~250W over 1h → 0.25 kWh
        assert 0.2 < body["kwh"] < 0.3

    def test_returns_cost(self, ctx_with_samples):
        code, body = api.handle_electricity(ctx_with_samples, {})
        # 0.25 kWh × 0.25 €/kWh = 0.0625 €
        assert 0.05 < body["cost"] < 0.08
        assert body["currency"] == "EUR"

    def test_includes_extrapolations(self, ctx_with_samples):
        _, body = api.handle_electricity(ctx_with_samples, {})
        # Daily extrapolation = 250W × 24h = 6 kWh × 0.25€ = 1.50€
        assert "daily_kwh" in body
        assert "monthly_cost" in body
        assert 5 < body["daily_kwh"] < 7

    def test_default_since_1h(self, ctx_with_samples):
        # Default window is 1 hour (3600s)
        code, body = api.handle_electricity(ctx_with_samples, {})
        assert body["window_seconds"] == 3600

    def test_custom_since(self, ctx_with_samples):
        code, body = api.handle_electricity(ctx_with_samples, {"since": "1800"})
        assert body["window_seconds"] == 1800

    def test_no_storage_returns_503(self):
        code, body = api.handle_electricity({"config": Config(defaults={})}, {})
        assert code == 503

    def test_invalid_since_returns_400(self, ctx_with_samples):
        code, body = api.handle_electricity(ctx_with_samples, {"since": "abc"})
        assert code == 400
