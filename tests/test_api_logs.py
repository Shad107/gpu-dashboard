"""Tests for /api/logs — log tail viewer."""
from __future__ import annotations

import pytest

from gpu_dashboard import api
from gpu_dashboard.config import Config


@pytest.fixture
def log_ctx(tmp_path):
    log = tmp_path / "dashboard.log"
    lines = [f"line {i}\n" for i in range(1, 201)]
    log.write_text("".join(lines))
    cfg = Config(defaults={"LOG_FILE": str(log)})
    return {"config": cfg}


class TestHandleLogs:
    def test_returns_last_100_by_default(self, log_ctx):
        code, body = api.handle_logs(log_ctx, {})
        assert code == 200
        assert body["ok"] is True
        # Default tail = 100
        assert len(body["lines"]) == 100
        # Should be the LAST 100, so first one is "line 101"
        assert body["lines"][0].strip() == "line 101"
        assert body["lines"][-1].strip() == "line 200"

    def test_custom_tail(self, log_ctx):
        code, body = api.handle_logs(log_ctx, {"tail": "20"})
        assert code == 200
        assert len(body["lines"]) == 20
        assert body["lines"][-1].strip() == "line 200"

    def test_tail_zero_returns_empty(self, log_ctx):
        code, body = api.handle_logs(log_ctx, {"tail": "0"})
        assert code == 200
        assert body["lines"] == []

    def test_no_log_configured(self):
        ctx = {"config": Config(defaults={})}
        code, body = api.handle_logs(ctx, {})
        assert code == 200
        assert body["ok"] is False
        assert "log" in body.get("reason", "").lower()

    def test_log_path_does_not_exist(self, tmp_path):
        cfg = Config(defaults={"LOG_FILE": str(tmp_path / "no-such-file.log")})
        code, body = api.handle_logs({"config": cfg}, {})
        assert code == 200
        assert body["ok"] is False
        assert "not found" in body.get("reason", "").lower() or "exist" in body.get("reason", "").lower()

    def test_invalid_tail_returns_400(self, log_ctx):
        code, body = api.handle_logs(log_ctx, {"tail": "abc"})
        assert code == 400


class TestHandleLogsJournalctl:
    """When LOG_FILE is empty but JOURNALCTL_UNIT is set, fall back to journalctl."""

    def test_journalctl_fallback(self, monkeypatch):
        import subprocess as sp
        cfg = Config(defaults={"JOURNALCTL_UNIT": "gpu-dashboard.service"})
        ctx = {"config": cfg}

        def fake_run(cmd, **kw):
            class R:
                returncode = 0
                stdout = "May 21 10:00 line A\nMay 21 10:01 line B\n"
                stderr = ""
            return R()
        monkeypatch.setattr(sp, "run", fake_run)
        code, body = api.handle_logs(ctx, {"tail": "10"})
        assert code == 200
        assert body["ok"] is True
        assert body["source"] == "journalctl"
        assert len(body["lines"]) == 2
