"""Tests for /api/llm/stats — parse llama-server /metrics for throughput."""
from __future__ import annotations

import pytest

from gpu_dashboard import api
from gpu_dashboard.config import Config


SAMPLE_LLAMACPP_METRICS = """
# HELP llamacpp:prompt_tokens_total Number of prompt tokens processed.
# TYPE llamacpp:prompt_tokens_total counter
llamacpp:prompt_tokens_total 12345
# HELP llamacpp:tokens_predicted_total Number of generation tokens processed.
# TYPE llamacpp:tokens_predicted_total counter
llamacpp:tokens_predicted_total 67890
# HELP llamacpp:n_decode_total Number of decode batches.
# TYPE llamacpp:n_decode_total counter
llamacpp:n_decode_total 543
# HELP llamacpp:requests_processing Number of requests being processed.
# TYPE llamacpp:requests_processing gauge
llamacpp:requests_processing 0
"""


class TestParseLlamacppMetrics:
    def test_parses_token_counters(self):
        result = api._parse_llamacpp_metrics(SAMPLE_LLAMACPP_METRICS)
        assert result["prompt_tokens_total"] == 12345
        assert result["tokens_predicted_total"] == 67890

    def test_handles_empty(self):
        assert api._parse_llamacpp_metrics("") == {}

    def test_ignores_help_type_lines(self):
        result = api._parse_llamacpp_metrics(SAMPLE_LLAMACPP_METRICS)
        # Shouldn't contain "HELP" or "TYPE" keys
        assert "HELP" not in result
        assert "TYPE" not in result


class TestHandleLlmStats:
    def test_no_url_returns_disabled(self):
        ctx = {"config": Config(defaults={"LLM_SERVER_URL": ""})}
        code, body = api.handle_llm_stats(ctx)
        assert code == 200
        assert body["available"] is False

    def test_unreachable_url_returns_unavailable(self):
        ctx = {"config": Config(defaults={"LLM_SERVER_URL": "http://127.0.0.1:1"})}
        code, body = api.handle_llm_stats(ctx)
        assert code == 200
        assert body["available"] is False

    def test_with_mocked_response(self, monkeypatch):
        import urllib.request

        class FakeResponse:
            def read(self):
                return SAMPLE_LLAMACPP_METRICS.encode("utf-8")
            def __enter__(self): return self
            def __exit__(self, *a): pass

        monkeypatch.setattr(urllib.request, "urlopen",
                            lambda *a, **kw: FakeResponse())
        ctx = {"config": Config(defaults={"LLM_SERVER_URL": "http://localhost:8080"})}
        code, body = api.handle_llm_stats(ctx)
        assert code == 200
        assert body["available"] is True
        assert body["tokens_generated_total"] == 67890
        assert body["prompt_tokens_total"] == 12345


class TestTokensPerWatt:
    def test_computes_ratio(self):
        # 1000 tokens / 250W average = 4 tokens/W
        ratio = api._tokens_per_watt(tokens=1000, avg_watts=250)
        assert 3.9 < ratio < 4.1

    def test_zero_watts_returns_none(self):
        assert api._tokens_per_watt(1000, 0) is None
