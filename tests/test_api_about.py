"""Tests for /api/about — version + build info."""
from __future__ import annotations

import time

import pytest

from gpu_dashboard import api, __version__


@pytest.fixture
def ctx(tmp_path):
    """Minimal context with a fake server start time."""
    return {
        "started_at": time.time() - 123,  # 2 min uptime
        "config_path": str(tmp_path / "config.env"),
    }


class TestHandleAbout:
    def test_returns_version(self, ctx):
        code, body = api.handle_about(ctx)
        assert code == 200
        assert body["version"] == __version__

    def test_returns_uptime_seconds(self, ctx):
        code, body = api.handle_about(ctx)
        # Uptime should be ~123s, allow some slack
        assert 100 < body["uptime_seconds"] < 200

    def test_returns_paths(self, ctx):
        code, body = api.handle_about(ctx)
        assert "config_path" in body
        assert "storage_path" in body

    def test_returns_python_version(self, ctx):
        code, body = api.handle_about(ctx)
        assert "python_version" in body
        # Should look like "3.x.y"
        assert body["python_version"].count(".") >= 1

    def test_returns_license_and_repo(self, ctx):
        code, body = api.handle_about(ctx)
        assert body["license"] == "MIT"
        assert "github.com/Shad107/gpu-dashboard" in body["repo_url"]

    def test_no_started_at_uses_now(self):
        code, body = api.handle_about({})
        assert body["uptime_seconds"] >= 0
        assert body["uptime_seconds"] < 5
