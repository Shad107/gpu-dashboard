"""Tests for /api/health — JSON status for external monitoring tools.

Returns 200 + {status: "ok", ...} if all critical components are up.
Returns 503 + {status: "degraded", ...} if any critical component is down.
"""
from __future__ import annotations

import time

import pytest

from gpu_dashboard import api


class FakeSampler:
    def __init__(self, running=True):
        self._thread = object() if running else None


class FakeStorage:
    def __init__(self, working=True):
        self.working = working
    def schema_version(self):
        if not self.working:
            raise RuntimeError("db closed")
        return 1


@pytest.fixture
def healthy_ctx():
    return {
        "started_at": time.time() - 60,
        "sampler": FakeSampler(running=True),
        "storage": FakeStorage(working=True),
    }


class TestHealthOK:
    def test_returns_200_when_all_components_up(self, healthy_ctx, monkeypatch):
        # Mock _gpu_card_snapshot to return alive
        monkeypatch.setattr(api._monolith, "_gpu_card_snapshot",
                            lambda: {"alive": True, "name": "RTX 3090"})
        code, body = api.handle_health(healthy_ctx)
        assert code == 200
        assert body["status"] == "ok"

    def test_includes_components(self, healthy_ctx, monkeypatch):
        monkeypatch.setattr(api._monolith, "_gpu_card_snapshot",
                            lambda: {"alive": True, "name": "X"})
        _, body = api.handle_health(healthy_ctx)
        assert body["components"]["gpu"] is True
        assert body["components"]["sampler"] is True
        assert body["components"]["storage"] is True

    def test_includes_uptime_and_version(self, healthy_ctx, monkeypatch):
        monkeypatch.setattr(api._monolith, "_gpu_card_snapshot",
                            lambda: {"alive": True, "name": "X"})
        _, body = api.handle_health(healthy_ctx)
        assert body["uptime_seconds"] >= 0
        assert "version" in body


class TestHealthDegraded:
    def test_gpu_down_returns_503(self, healthy_ctx, monkeypatch):
        monkeypatch.setattr(api._monolith, "_gpu_card_snapshot",
                            lambda: {"alive": False, "name": "?"})
        code, body = api.handle_health(healthy_ctx)
        assert code == 503
        assert body["status"] == "degraded"
        assert body["components"]["gpu"] is False

    def test_sampler_missing_returns_503(self, monkeypatch):
        monkeypatch.setattr(api._monolith, "_gpu_card_snapshot",
                            lambda: {"alive": True, "name": "X"})
        ctx = {"started_at": time.time(), "sampler": None, "storage": FakeStorage()}
        code, body = api.handle_health(ctx)
        assert code == 503
        assert body["components"]["sampler"] is False

    def test_storage_broken_returns_503(self, monkeypatch):
        monkeypatch.setattr(api._monolith, "_gpu_card_snapshot",
                            lambda: {"alive": True, "name": "X"})
        ctx = {"started_at": time.time(), "sampler": FakeSampler(),
               "storage": FakeStorage(working=False)}
        code, body = api.handle_health(ctx)
        assert code == 503
        assert body["components"]["storage"] is False
