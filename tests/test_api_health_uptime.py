"""Tests for uptime metrics added to /api/health (R&D #3.3, cycle 136)."""
import time

import pytest

from gpu_dashboard import api
from gpu_dashboard.storage import Storage


@pytest.fixture
def ctx(tmp_path):
    s = Storage(str(tmp_path / "metrics.db"))
    yield {"storage": s, "config": None, "sampler": None,
           "started_at": time.time() - 3600}
    s.close()


def test_new_fields_present(ctx):
    code, body = api.handle_health(ctx)
    for field in ("up_minutes_24h", "uptime_pct_24h",
                  "restart_count_24h", "sampler_alive"):
        assert field in body, f"missing field {field}"


def test_empty_db_zero_uptime(ctx):
    _, body = api.handle_health(ctx)
    assert body["up_minutes_24h"] == 0
    assert body["uptime_pct_24h"] == 0.0


def test_uptime_counts_distinct_minutes(ctx):
    """30 samples spread across 30 distinct minutes → up_minutes_24h = 30."""
    now = int(time.time())
    for i in range(30):
        ctx["storage"].record_sample({"ts": now - i * 60, "power": 100, "gpu_index": 0})
    _, body = api.handle_health(ctx)
    assert body["up_minutes_24h"] == 30
    # 30 / 1440 * 100 = 2.1
    assert body["uptime_pct_24h"] == pytest.approx(2.1, abs=0.1)


def test_uptime_pct_caps_at_100(ctx):
    """At 1 sample per minute for 24h, uptime_pct_24h should be 100."""
    now = int(time.time())
    for i in range(1440):
        ctx["storage"].record_sample({"ts": now - i * 60, "power": 100, "gpu_index": 0})
    _, body = api.handle_health(ctx)
    assert body["uptime_pct_24h"] == 100.0


def test_restart_count_detects_gaps(ctx):
    """3 samples with > 5 min gaps in between → restart_count_24h = 2 (2 gaps)."""
    now = int(time.time())
    # ts=now-3600 (1h ago) → big gap → ts=now-1800 (30 min ago) → big gap → ts=now
    ctx["storage"].record_sample({"ts": now - 3600, "power": 100, "gpu_index": 0})
    ctx["storage"].record_sample({"ts": now - 1800, "power": 100, "gpu_index": 0})
    ctx["storage"].record_sample({"ts": now, "power": 100, "gpu_index": 0})
    _, body = api.handle_health(ctx)
    assert body["restart_count_24h"] == 2


def test_no_storage_returns_zeros():
    code, body = api.handle_health({"started_at": time.time()})
    assert body["up_minutes_24h"] == 0
    assert body["uptime_pct_24h"] == 0
    assert body["restart_count_24h"] == 0
