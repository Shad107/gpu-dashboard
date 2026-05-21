"""Tests for /api/profile-stats — time spent in each power profile.

Logic : walks the events list filtered to kind='profile_switch', computes
gaps between consecutive switches. The interval [last_switch, now] is
attributed to the last-switched-to profile.
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


def _switch(storage, ts: int, to: str):
    """Insert a profile_switch event at the given ts (we override the ts the
    Storage layer uses by directly INSERTing into the events table)."""
    import json
    storage._conn.execute(
        "INSERT INTO events (ts, kind, payload) VALUES (?, ?, ?)",
        (ts, "profile_switch", json.dumps({"to": to})),
    )
    storage._conn.commit()


class TestProfileStats:
    def test_no_events_returns_unknown(self, ctx):
        code, body = api.handle_profile_stats(ctx, {})
        assert code == 200
        assert body["totals"] == {}

    def test_single_switch_attributes_all_time(self, ctx, monkeypatch):
        now = 1_700_000_000
        monkeypatch.setattr(time, "time", lambda: now)
        _switch(ctx["storage"], now - 3600, "sweet")  # 1 hour ago
        code, body = api.handle_profile_stats(ctx, {})
        # 1 hour of "sweet"
        assert body["totals"]["sweet"] == 3600

    def test_two_switches_partition_time(self, ctx, monkeypatch):
        now = 1_700_000_000
        monkeypatch.setattr(time, "time", lambda: now)
        _switch(ctx["storage"], now - 3600, "boost")   # 1h ago → boost
        _switch(ctx["storage"], now - 1800, "silent")  # 30 min ago → silent
        code, body = api.handle_profile_stats(ctx, {})
        # boost: 1800s, silent: 1800s
        assert body["totals"]["boost"] == 1800
        assert body["totals"]["silent"] == 1800

    def test_since_filter(self, ctx, monkeypatch):
        now = 1_700_000_000
        monkeypatch.setattr(time, "time", lambda: now)
        _switch(ctx["storage"], now - 7200, "silent")  # 2h ago — outside since=3600
        _switch(ctx["storage"], now - 3600, "boost")   # 1h ago
        code, body = api.handle_profile_stats(ctx, {"since": "3600"})
        # The boost run started 1h ago. Anything older is clipped.
        # silent gets 0s (or absent) because the "silent → boost" transition
        # happened at the window start.
        assert body["totals"].get("boost") == 3600
        assert body["totals"].get("silent", 0) == 0
