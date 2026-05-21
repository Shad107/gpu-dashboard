"""Tests pour les endpoints API d'historique : /api/history, /api/events, /api/export.

Chaque handler prend (ctx, query_params) → (code, body).
Le body est un dict JSON pour history/events, une string CSV pour export.
"""
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


def _add_sample(storage, ts, **fields):
    base = {"ts": ts, "temp": 50.0, "fan_pct": 40, "power": 200.0,
            "power_limit": 250.0, "util_gpu": 75, "mem_used_mib": 12000}
    base.update(fields)
    storage.record_sample(base)


# ──────────────────────────── handle_history ──────────────────────────────


class TestHandleHistory:
    def test_no_storage_returns_503(self):
        code, body = api.handle_history({}, {})
        assert code == 503
        assert body["ok"] is False

    def test_empty_returns_ok_empty_list(self, ctx):
        code, body = api.handle_history(ctx, {})
        assert code == 200
        assert body["ok"] is True
        assert body["samples"] == []

    def test_returns_samples(self, ctx):
        _add_sample(ctx["storage"], 100)
        _add_sample(ctx["storage"], 200)
        code, body = api.handle_history(ctx, {"from": "0"})
        assert code == 200
        assert len(body["samples"]) == 2

    def test_filter_from_ts(self, ctx):
        _add_sample(ctx["storage"], 100, temp=40)
        _add_sample(ctx["storage"], 200, temp=60)
        code, body = api.handle_history(ctx, {"from": "150"})
        assert code == 200
        assert [s["ts"] for s in body["samples"]] == [200]

    def test_filter_to_ts(self, ctx):
        _add_sample(ctx["storage"], 100)
        _add_sample(ctx["storage"], 200)
        _add_sample(ctx["storage"], 300)
        code, body = api.handle_history(ctx, {"from": "0", "to": "250"})
        assert [s["ts"] for s in body["samples"]] == [100, 200]

    def test_with_step_resamples(self, ctx):
        for ts, temp in [(100, 40), (115, 50), (130, 80), (170, 90)]:
            _add_sample(ctx["storage"], ts, temp=temp)
        code, body = api.handle_history(ctx, {"from": "0", "to": "300", "step": "60"})
        assert code == 200
        # 2 bins attendus (60 et 120)
        assert len(body["samples"]) == 2

    def test_invalid_from_returns_400(self, ctx):
        code, body = api.handle_history(ctx, {"from": "not-a-number"})
        assert code == 400
        assert body["ok"] is False


# ──────────────────────────── handle_events ───────────────────────────────


class TestHandleEvents:
    def test_no_storage_returns_503(self):
        code, body = api.handle_events({}, {})
        assert code == 503

    def test_empty_returns_ok(self, ctx):
        code, body = api.handle_events(ctx, {})
        assert code == 200
        assert body["events"] == []

    def test_returns_events(self, ctx):
        ctx["storage"].record_event("drop", {"reason": "OcuLink"})
        ctx["storage"].record_event("recover")
        code, body = api.handle_events(ctx, {"from": "0"})
        assert code == 200
        assert len(body["events"]) == 2

    def test_filter_by_kind(self, ctx):
        ctx["storage"].record_event("drop")
        ctx["storage"].record_event("recover")
        ctx["storage"].record_event("drop")
        code, body = api.handle_events(ctx, {"from": "0", "kind": "drop"})
        assert code == 200
        assert len(body["events"]) == 2
        assert all(e["kind"] == "drop" for e in body["events"])

    def test_invalid_from_returns_400(self, ctx):
        code, body = api.handle_events(ctx, {"from": "abc"})
        assert code == 400


# ──────────────────────────── handle_export ───────────────────────────────


class TestHandleExport:
    def test_no_storage_returns_503(self):
        code, body = api.handle_export({}, {})
        assert code == 503
        # 503 → body est un dict (erreur JSON), pas une string CSV
        assert isinstance(body, dict)

    def test_empty_returns_header_only_csv(self, ctx):
        code, body = api.handle_export(ctx, {})
        assert code == 200
        # body est une string CSV
        assert isinstance(body, str)
        lines = body.strip().splitlines()
        # juste le header
        assert len(lines) == 1
        assert "ts" in lines[0]

    def test_with_samples(self, ctx):
        _add_sample(ctx["storage"], 100, temp=42.0)
        _add_sample(ctx["storage"], 200, temp=55.0)
        code, body = api.handle_export(ctx, {"since": "0"})
        assert code == 200
        lines = body.strip().splitlines()
        assert len(lines) == 3  # header + 2 rows

    def test_since_filter(self, ctx):
        _add_sample(ctx["storage"], 100)
        _add_sample(ctx["storage"], 200)
        _add_sample(ctx["storage"], 300)
        code, body = api.handle_export(ctx, {"since": "150"})
        lines = body.strip().splitlines()
        # header + 2 rows (200 et 300)
        assert len(lines) == 3

    def test_unsupported_format_returns_400(self, ctx):
        code, body = api.handle_export(ctx, {"format": "xml"})
        assert code == 400
        assert isinstance(body, dict)

    def test_invalid_since_returns_400(self, ctx):
        code, body = api.handle_export(ctx, {"since": "abc"})
        assert code == 400
