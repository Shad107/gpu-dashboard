"""Tests for /api/llm/lifetime — cumulative LLM stats from DB."""
from __future__ import annotations

import time

import pytest

from gpu_dashboard import api
from gpu_dashboard.storage import Storage


@pytest.fixture
def ctx(tmp_path):
    storage = Storage(str(tmp_path / "metrics.db"))
    yield {"storage": storage}
    storage.close()


def _sample(ts, tokens=None, power=200.0):
    return {"ts": ts, "temp": 50, "power": power,
            "tokens_total_snapshot": tokens}


class TestLlmLifetime:
    def test_no_storage_returns_503(self):
        code, body = api.handle_llm_lifetime({})
        assert code == 503

    def test_empty_db_returns_zeros(self, ctx):
        code, body = api.handle_llm_lifetime(ctx)
        assert code == 200
        assert body["available"] is False  # no llm samples
        assert body["total_tokens_generated"] == 0

    def test_no_token_samples_returns_zero(self, ctx):
        # samples without tokens (LLM_SERVER_URL not configured)
        ctx["storage"].record_sample(_sample(100, tokens=None))
        ctx["storage"].record_sample(_sample(200, tokens=None))
        code, body = api.handle_llm_lifetime(ctx)
        assert body["available"] is False

    def test_single_token_sample(self, ctx):
        ctx["storage"].record_sample(_sample(100, tokens=12345))
        code, body = api.handle_llm_lifetime(ctx)
        assert body["available"] is True
        # No prior sample, so total generated = 0 (can't compute delta)
        assert body["total_tokens_generated"] == 0
        assert body["latest_snapshot"] == 12345

    def test_two_samples_delta(self, ctx):
        ctx["storage"].record_sample(_sample(100, tokens=1000, power=100))
        ctx["storage"].record_sample(_sample(200, tokens=1500, power=100))
        code, body = api.handle_llm_lifetime(ctx)
        assert body["total_tokens_generated"] == 500
        # 500 tokens / 100s / 100W = 0.05 tok/W
        assert body["avg_tokens_per_watt"] == pytest.approx(0.05, rel=0.05)

    def test_restart_ignored(self, ctx):
        """When tokens counter resets (llama-server restart), the negative delta is treated as 0."""
        ctx["storage"].record_sample(_sample(100, tokens=1000))
        ctx["storage"].record_sample(_sample(200, tokens=2000))  # +1000
        ctx["storage"].record_sample(_sample(300, tokens=50))    # restart, ignored
        ctx["storage"].record_sample(_sample(400, tokens=150))   # +100
        code, body = api.handle_llm_lifetime(ctx)
        assert body["total_tokens_generated"] == 1100
        assert body["restart_count"] == 1

    def test_since_timestamp_provided(self, ctx):
        ctx["storage"].record_sample(_sample(1000, tokens=10))
        ctx["storage"].record_sample(_sample(2000, tokens=20))
        code, body = api.handle_llm_lifetime(ctx)
        # since_ts is the first sample's timestamp
        assert body["since_ts"] == 1000
