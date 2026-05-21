"""Tests for /api/llm/perf, /api/thermal-stats, /api/power-stats.

These endpoints power the rewritten Stats page (cycle 74). They return both
- rolling-window aggregates (avg, peak)
- a small downsampled series suitable for a sparkline (60 pts for 1h, 24 pts for 24h)
"""
from __future__ import annotations

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


def _samp(ts, power=100.0, temp=50, tokens=None):
    return {"ts": ts, "power": power, "temp": temp, "tokens_total_snapshot": tokens}


# ─── /api/llm/perf ─────────────────────────────────────────────────────────

class TestLlmPerf:
    def test_no_storage_returns_503(self):
        code, body = api.handle_llm_perf({})
        assert code == 503

    def test_empty_db_returns_unavailable(self, ctx):
        code, body = api.handle_llm_perf(ctx)
        assert code == 200
        assert body["available"] is False

    def test_no_tokens_returns_unavailable(self, ctx):
        ctx["storage"].record_sample(_samp(100, tokens=None))
        ctx["storage"].record_sample(_samp(200, tokens=None))
        code, body = api.handle_llm_perf(ctx)
        assert body["available"] is False

    def test_rolling_windows(self, ctx, monkeypatch):
        now = 2_000_000_000
        monkeypatch.setattr(time, "time", lambda: now)
        # Generate samples at 60s spacing over the last 90 min
        # tokens grow by 600/min = 10 tok/s, then last 5 min by 60/min = 1 tok/s
        tokens = 0
        for i in range(90):
            ts = now - (90 - i) * 60
            if i > 85:
                tokens += 60   # last 5 min : 1 tok/s
            else:
                tokens += 600  # 10 tok/s
            ctx["storage"].record_sample(_samp(ts, tokens=tokens))
        code, body = api.handle_llm_perf(ctx)
        assert body["available"] is True
        # 1-min window has very few samples → won't be tested precisely
        # 5-min window should be ~1 tok/s (latest behaviour)
        assert body["avg_tps_5m"] == pytest.approx(1.0, rel=0.5)
        # 1h window should be ~10 tok/s
        assert body["avg_tps_1h"] == pytest.approx(10.0, rel=0.5)

    def test_returns_sparkline_series(self, ctx, monkeypatch):
        now = 2_000_000_000
        monkeypatch.setattr(time, "time", lambda: now)
        tokens = 0
        for i in range(60):
            tokens += 600
            ctx["storage"].record_sample(_samp(now - (60 - i) * 60, tokens=tokens))
        _, body = api.handle_llm_perf(ctx)
        # series has length up to 60 (1 point per minute over last 60 min)
        assert "series_1h" in body
        assert 0 < len(body["series_1h"]) <= 60


# ─── /api/thermal-stats ────────────────────────────────────────────────────

class TestThermalStats:
    def test_empty(self, ctx):
        code, body = api.handle_thermal_stats(ctx)
        assert code == 200
        assert body["avg_temp_24h"] == 0
        assert body["peak_temp_24h"] == 0
        assert body["time_above_80c_seconds"] == 0

    def test_normal(self, ctx, monkeypatch):
        now = 2_000_000_000
        monkeypatch.setattr(time, "time", lambda: now)
        for i in range(24):
            ctx["storage"].record_sample(_samp(now - (24 - i) * 3600, temp=40 + i))
        _, body = api.handle_thermal_stats(ctx)
        # avg = (40+41+...+63) / 24 = 51.5
        assert body["avg_temp_24h"] == pytest.approx(51.5, abs=1.0)
        assert body["peak_temp_24h"] == 63

    def test_time_above_threshold(self, ctx, monkeypatch):
        now = 2_000_000_000
        monkeypatch.setattr(time, "time", lambda: now)
        # 3 samples at >80C, spaced 60s apart → 2 intervals of 60s = 120s above
        # (we only count contiguous samples that are both >80, conservatively)
        for i in range(3):
            ctx["storage"].record_sample(_samp(now - 7 * 86400 + i * 60, temp=85))
        _, body = api.handle_thermal_stats(ctx)
        assert body["time_above_80c_seconds"] > 0

    def test_returns_24h_series(self, ctx):
        _, body = api.handle_thermal_stats(ctx)
        assert "series_24h" in body
        assert isinstance(body["series_24h"], list)


# ─── /api/power-stats ──────────────────────────────────────────────────────

class TestPowerStats:
    def test_empty(self, ctx):
        code, body = api.handle_power_stats(ctx)
        assert code == 200
        assert body["avg_watts_24h"] == 0
        assert body["peak_watts_24h"] == 0
        assert body["kwh_today"] == 0

    def test_normal(self, ctx, monkeypatch):
        now = 2_000_000_000
        monkeypatch.setattr(time, "time", lambda: now)
        for i in range(24):
            ctx["storage"].record_sample(_samp(now - (24 - i) * 3600, power=50 + i * 5))
        _, body = api.handle_power_stats(ctx)
        # avg = (50+55+60+...+165)/24 = 107.5
        assert body["avg_watts_24h"] == pytest.approx(107.5, abs=1.0)
        assert body["peak_watts_24h"] == 165

    def test_uses_electricity_price(self, ctx, monkeypatch):
        now = 2_000_000_000
        monkeypatch.setattr(time, "time", lambda: now)
        # 1000W for 1 hour = 1 kWh = 0.25 €
        for i in range(2):
            ctx["storage"].record_sample(_samp(now - 30 * 60 + i * 60, power=1000.0))
        _, body = api.handle_power_stats(ctx)
        assert body["kwh_today"] > 0
        assert body["cost_today"] > 0

    def test_returns_24h_series(self, ctx):
        _, body = api.handle_power_stats(ctx)
        assert "series_24h" in body
        assert isinstance(body["series_24h"], list)
