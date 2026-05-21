"""Tests for the sampler's LLM token fetcher."""
from __future__ import annotations

import pytest

from gpu_dashboard.metrics import MetricsSampler


SAMPLE_METRICS = """
# HELP llamacpp:tokens_predicted_total Number of generation tokens processed.
# TYPE llamacpp:tokens_predicted_total counter
llamacpp:tokens_predicted_total 67890
"""


class TestFetchLlmTokens:
    def test_no_url_returns_none(self):
        s = MetricsSampler(llm_server_url=None)
        assert s._fetch_llm_tokens() is None

    def test_empty_url_returns_none(self):
        s = MetricsSampler(llm_server_url="")
        assert s._fetch_llm_tokens() is None

    def test_unreachable_returns_none(self):
        s = MetricsSampler(llm_server_url="http://127.0.0.1:1")
        assert s._fetch_llm_tokens() is None  # connection refused

    def test_parses_tokens_from_response(self, monkeypatch):
        import urllib.request
        class FakeResp:
            def read(self): return SAMPLE_METRICS.encode("utf-8")
            def __enter__(self): return self
            def __exit__(self, *a): pass
        monkeypatch.setattr(urllib.request, "urlopen",
                            lambda *a, **kw: FakeResp())
        s = MetricsSampler(llm_server_url="http://localhost:8080")
        assert s._fetch_llm_tokens() == 67890

    def test_response_without_target_metric(self, monkeypatch):
        import urllib.request
        class FakeResp:
            def read(self): return b"# unrelated\nother_metric 42\n"
            def __enter__(self): return self
            def __exit__(self, *a): pass
        monkeypatch.setattr(urllib.request, "urlopen",
                            lambda *a, **kw: FakeResp())
        s = MetricsSampler(llm_server_url="http://localhost:8080")
        assert s._fetch_llm_tokens() is None
