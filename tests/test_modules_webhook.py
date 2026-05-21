"""Tests for the webhook notification backend (URL-shape detection + send)."""
from __future__ import annotations

import json
from unittest.mock import MagicMock, patch

import pytest

from gpu_dashboard.modules.webhook import _payload_for_url, send


class TestPayloadShape:
    def test_discord_uses_content(self):
        url = "https://discord.com/api/webhooks/123/abc"
        p = _payload_for_url(url, "hello", "info")
        assert p == {"content": "hello"}

    def test_discordapp_alias(self):
        url = "https://discordapp.com/api/webhooks/123/abc"
        p = _payload_for_url(url, "hello", "info")
        assert "content" in p

    def test_slack_uses_text(self):
        url = "https://hooks.slack.com/services/T1/B1/X1"
        p = _payload_for_url(url, "hello", "drop")
        assert p == {"text": "hello"}

    def test_generic_includes_metadata(self):
        url = "https://example.com/webhook"
        p = _payload_for_url(url, "hello", "drop")
        assert p["text"] == "hello"
        assert p["kind"] == "drop"
        assert p["source"] == "gpu-dashboard"
        assert "timestamp" in p
        assert isinstance(p["timestamp"], int)


class TestSend:
    def test_no_url_returns_false(self):
        ok, msg = send("", "test")
        assert ok is False

    def test_calls_urlopen_with_correct_data(self, monkeypatch):
        captured = {}
        class FakeResp:
            status = 200
            def __enter__(self): return self
            def __exit__(self, *a): pass
        def fake_urlopen(req, timeout=None):
            captured["url"] = req.full_url
            captured["headers"] = dict(req.headers)
            captured["data"] = json.loads(req.data.decode("utf-8"))
            captured["timeout"] = timeout
            return FakeResp()
        import urllib.request as ur
        monkeypatch.setattr(ur, "urlopen", fake_urlopen)
        ok, msg = send("https://example.com/hook", "ping")
        assert ok is True
        assert captured["url"] == "https://example.com/hook"
        assert captured["data"]["text"] == "ping"
        assert "User-Agent" in captured["headers"] or "User-agent" in captured["headers"]

    def test_http_error_returns_false(self, monkeypatch):
        import urllib.error as ue
        import urllib.request as ur
        def fake_urlopen(req, timeout=None):
            raise ue.HTTPError(req.full_url, 500, "Internal Server Error", {}, None)
        monkeypatch.setattr(ur, "urlopen", fake_urlopen)
        ok, msg = send("https://example.com/hook", "ping")
        assert ok is False
        assert "500" in msg

    def test_connection_error_returns_false(self, monkeypatch):
        import urllib.error as ue
        import urllib.request as ur
        def fake_urlopen(req, timeout=None):
            raise ue.URLError("no route to host")
        monkeypatch.setattr(ur, "urlopen", fake_urlopen)
        ok, msg = send("https://example.com/hook", "ping")
        assert ok is False
        assert "connection" in msg.lower()

    def test_204_no_content_is_ok(self, monkeypatch):
        """Discord returns 204 on success — should still be ok."""
        class FakeResp:
            status = 204
            def __enter__(self): return self
            def __exit__(self, *a): pass
        import urllib.request as ur
        monkeypatch.setattr(ur, "urlopen", lambda req, timeout=None: FakeResp())
        ok, msg = send("https://discord.com/api/webhooks/x/y", "test")
        assert ok is True
