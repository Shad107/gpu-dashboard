"""Tests for POST /api/electricity/config — runtime edit of electricity rate."""
from __future__ import annotations

import os

import pytest

from gpu_dashboard import api
from gpu_dashboard.config import Config


@pytest.fixture
def ctx(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    cfg_path = tmp_path / ".config" / "gpu-dashboard" / "config.env"
    cfg_path.parent.mkdir(parents=True, exist_ok=True)
    cfg_path.write_text("DASHBOARD_PORT=9999\nELECTRICITY_PRICE_EUR_PER_KWH=0.25\n")
    cfg = Config(defaults={"ELECTRICITY_PRICE_EUR_PER_KWH": "0.25", "ELECTRICITY_CURRENCY": "EUR"},
                 files=[str(cfg_path)])
    return {"config": cfg, "config_path": str(cfg_path)}


class TestElectricityConfigSave:
    def test_saves_price(self, ctx):
        code, body = api.handle_electricity_config(ctx,
            {"price_per_kwh": 0.18, "currency": "EUR"})
        assert code == 200
        assert body["ok"] is True
        # Should have been written to config.env
        with open(ctx["config_path"]) as f:
            content = f.read()
        assert "ELECTRICITY_PRICE_EUR_PER_KWH=0.18" in content

    def test_saves_currency(self, ctx):
        code, body = api.handle_electricity_config(ctx,
            {"price_per_kwh": 0.16, "currency": "USD"})
        assert code == 200
        with open(ctx["config_path"]) as f:
            content = f.read()
        assert "ELECTRICITY_CURRENCY=USD" in content

    def test_updates_in_memory_config(self, ctx):
        api.handle_electricity_config(ctx, {"price_per_kwh": 0.30, "currency": "EUR"})
        # Subsequent /api/electricity should use the new price (the Config is
        # updated in-place via .set so no restart needed).
        # Compare as float to avoid Python's trailing-zero strip ("0.30" → "0.3")
        assert float(ctx["config"].get("ELECTRICITY_PRICE_EUR_PER_KWH")) == 0.30

    def test_invalid_price_returns_400(self, ctx):
        code, body = api.handle_electricity_config(ctx,
            {"price_per_kwh": "not-a-number"})
        assert code == 400
        assert body["ok"] is False

    def test_negative_price_returns_400(self, ctx):
        code, body = api.handle_electricity_config(ctx,
            {"price_per_kwh": -0.5})
        assert code == 400

    def test_unreasonable_price_returns_400(self, ctx):
        # > 5 €/kWh is clearly unreasonable
        code, body = api.handle_electricity_config(ctx,
            {"price_per_kwh": 50.0})
        assert code == 400
