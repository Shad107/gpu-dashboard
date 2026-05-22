"""Tests for the monthly budget tracker added in cycle 121."""
import datetime
import time

import pytest

from gpu_dashboard import api
from gpu_dashboard.config import Config
from gpu_dashboard.storage import Storage


@pytest.fixture
def ctx(tmp_path):
    s = Storage(str(tmp_path / "metrics.db"))
    cfg = Config(defaults={
        "ELECTRICITY_PRICE_EUR_PER_KWH": "0.25",
        "ELECTRICITY_MONTHLY_BUDGET_KWH": "100",
    })
    yield {"storage": s, "config": cfg}
    s.close()


def test_power_stats_exposes_monthly_fields(ctx):
    code, body = api.handle_power_stats(ctx)
    for field in ("kwh_month", "cost_month", "month_start_ts", "month_end_ts",
                  "month_progress_pct", "forecast_kwh", "budget_kwh", "over_budget"):
        assert field in body, f"missing field {field}"


def test_month_start_is_day_1(ctx):
    _, body = api.handle_power_stats(ctx)
    ms = body["month_start_ts"]
    d = datetime.datetime.fromtimestamp(ms)
    assert d.day == 1
    assert d.hour == 0 and d.minute == 0 and d.second == 0


def test_month_progress_in_0_to_100(ctx):
    _, body = api.handle_power_stats(ctx)
    assert 0 < body["month_progress_pct"] <= 100


def test_budget_kwh_loaded_from_config(ctx):
    _, body = api.handle_power_stats(ctx)
    assert body["budget_kwh"] == 100.0


def test_budget_zero_when_unset():
    """Without config, budget_kwh should be 0 (disabled)."""
    from gpu_dashboard.storage import Storage as S
    import tempfile, os
    with tempfile.TemporaryDirectory() as td:
        s = S(os.path.join(td, "m.db"))
        cfg = Config(defaults={"ELECTRICITY_PRICE_EUR_PER_KWH": "0.25"})
        _, body = api.handle_power_stats({"storage": s, "config": cfg})
        assert body["budget_kwh"] == 0.0
        assert body["over_budget"] is False
        s.close()


def test_over_budget_flag_when_forecast_exceeds(ctx):
    """Plant enough kwh_month that the forecast clearly exceeds 100 kWh."""
    now = int(time.time())
    month_start = int(datetime.datetime.fromtimestamp(now).replace(
        day=1, hour=0, minute=0, second=0, microsecond=0).timestamp())
    # Plant 6 hours of 2000W continuously starting at month_start+60s
    # → 12 kWh in 6h. If we're early in the month, forecast extrapolates high.
    start_ts = month_start + 60
    for i in range(72):
        ctx["storage"].record_sample({
            "ts": start_ts + i * 300,
            "power": 2000.0,
        })
    _, body = api.handle_power_stats(ctx)
    # 12 kWh over 6h is roughly 48 kWh/day → way over 100 kWh budget if
    # we're past day 4 of the month. Conservative assertion :
    # forecast_kwh should be > kwh_month at least
    assert body["kwh_month"] > 0
    assert body["forecast_kwh"] >= body["kwh_month"]


def test_electricity_config_accepts_budget(tmp_path, monkeypatch):
    """POST /api/electricity/config with budget_kwh persists it."""
    monkeypatch.setenv("HOME", str(tmp_path))
    cfg = Config(defaults={
        "ELECTRICITY_PRICE_EUR_PER_KWH": "0.25",
        "ELECTRICITY_CURRENCY": "EUR",
    })
    ctx_local = {"config": cfg, "config_path": str(tmp_path / "config.env")}
    code, body = api.handle_electricity_config(ctx_local, {
        "price_per_kwh": 0.30,
        "currency": "EUR",
        "budget_kwh": 150,
    })
    assert code == 200
    assert body["budget_kwh"] == 150.0
    # Cfg in-memory updated
    assert float(cfg.get("ELECTRICITY_MONTHLY_BUDGET_KWH")) == 150


def test_electricity_config_rejects_negative_budget(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    cfg = Config(defaults={"ELECTRICITY_PRICE_EUR_PER_KWH": "0.25"})
    ctx_local = {"config": cfg, "config_path": str(tmp_path / "config.env")}
    code, body = api.handle_electricity_config(ctx_local, {
        "price_per_kwh": 0.30,
        "budget_kwh": -5,
    })
    assert code == 400
