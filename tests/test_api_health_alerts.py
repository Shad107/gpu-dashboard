"""Tests for the recent_alerts field added to /api/health (cycle 102)."""
import json
import pytest

from gpu_dashboard import api
from gpu_dashboard.storage import Storage


@pytest.fixture
def ctx(tmp_path):
    storage = Storage(str(tmp_path / "metrics.db"))
    yield {"storage": storage, "config": None, "sampler": None}
    storage.close()


def _add_alert(s, ts, kind="gpu_temp_high", value=92):
    s._conn.execute(
        "INSERT INTO events (ts, kind, payload) VALUES (?, ?, ?)",
        (ts, "alert", json.dumps({"kind": kind, "value": value, "threshold": 85})),
    )
    s._conn.commit()


def test_health_returns_recent_alerts_field(ctx):
    code, body = api.handle_health(ctx)
    assert "recent_alerts" in body
    assert isinstance(body["recent_alerts"], list)


def test_health_no_alerts_returns_empty(ctx):
    code, body = api.handle_health(ctx)
    assert body["recent_alerts"] == []


def test_health_returns_alerts_most_recent_first(ctx):
    import time
    now = int(time.time())
    _add_alert(ctx["storage"], now - 600, kind="old_alert")
    _add_alert(ctx["storage"], now - 60, kind="newer_alert")
    _, body = api.handle_health(ctx)
    assert len(body["recent_alerts"]) == 2
    assert body["recent_alerts"][0]["payload"]["kind"] == "newer_alert"
    assert body["recent_alerts"][1]["payload"]["kind"] == "old_alert"


def test_health_caps_at_5_alerts(ctx):
    import time
    now = int(time.time())
    for i in range(10):
        _add_alert(ctx["storage"], now - i * 60)
    _, body = api.handle_health(ctx)
    assert len(body["recent_alerts"]) == 5


def test_health_only_picks_alert_events_not_other(ctx):
    import time
    now = int(time.time())
    _add_alert(ctx["storage"], now - 100)
    ctx["storage"].record_event("profile_switch", {"to": "boost"})
    ctx["storage"].record_event("drop", {})
    _, body = api.handle_health(ctx)
    assert len(body["recent_alerts"]) == 1  # only the 'alert' event
