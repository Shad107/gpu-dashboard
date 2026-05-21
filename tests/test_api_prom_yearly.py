"""Tests for the yearly Prometheus gauges added in cycle 105."""
import json
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
        "GPU_INDEX": "0",
    })
    yield {"storage": s, "config": cfg}
    s.close()


def test_prom_exposes_yearly_kwh(ctx):
    code, body = api.handle_prom(ctx)
    assert code == 200
    assert "gpu_dashboard_kwh_year" in body
    assert "gpu_dashboard_cost_year" in body
    assert "gpu_dashboard_kwh_today" in body


def test_prom_no_llm_no_tokens_metric(ctx):
    """Without samples that have tokens, the LLM gauges are skipped."""
    code, body = api.handle_prom(ctx)
    assert "gpu_dashboard_tokens_year_total" not in body
    assert "gpu_dashboard_tokens_lifetime_total" not in body


def test_prom_with_llm_emits_tokens(ctx):
    """Samples with tokens → tokens_year + tokens_lifetime gauges appear."""
    now = int(time.time())
    ctx["storage"].record_sample({"ts": now - 600, "power": 100, "tokens_total_snapshot": 0})
    ctx["storage"].record_sample({"ts": now - 100, "power": 100, "tokens_total_snapshot": 50_000})
    code, body = api.handle_prom(ctx)
    assert "gpu_dashboard_tokens_lifetime_total" in body
    assert "gpu_dashboard_tokens_year_total" in body
    # Sanity : the values are present and parseable
    assert "50000" in body or "50_000" in body or "50000.0" in body


def test_prom_latest_alert_age(ctx):
    """When alerts exist, expose the age of the most recent one."""
    now = int(time.time())
    ctx["storage"]._conn.execute(
        "INSERT INTO events (ts, kind, payload) VALUES (?, ?, ?)",
        (now - 60, "alert", json.dumps({"kind": "gpu_temp_high"})),
    )
    ctx["storage"]._conn.commit()
    code, body = api.handle_prom(ctx)
    assert "gpu_dashboard_latest_alert_age_seconds" in body


def test_prom_no_alert_no_age_metric(ctx):
    code, body = api.handle_prom(ctx)
    assert "gpu_dashboard_latest_alert_age_seconds" not in body
