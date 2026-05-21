"""Tests for /api/alerts/latest — most recent alert event."""
import json
import pytest

from gpu_dashboard import api
from gpu_dashboard.storage import Storage


@pytest.fixture
def ctx(tmp_path):
    s = Storage(str(tmp_path / "metrics.db"))
    yield {"storage": s}
    s.close()


def _add_alert(s, ts, kind, value=85, threshold=80):
    s._conn.execute(
        "INSERT INTO events (ts, kind, payload) VALUES (?, ?, ?)",
        (ts, "alert", json.dumps({"kind": kind, "value": value, "threshold": threshold})),
    )
    s._conn.commit()


def test_no_storage_returns_503():
    code, body = api.handle_alerts_latest({})
    assert code == 503


def test_empty_returns_null(ctx):
    code, body = api.handle_alerts_latest(ctx)
    assert code == 200
    assert body["alert"] is None


def test_returns_most_recent(ctx):
    _add_alert(ctx["storage"], 1000, "gpu_temp_high")
    _add_alert(ctx["storage"], 2000, "fan_pct_high")
    _add_alert(ctx["storage"], 1500, "mem_temp_high")
    code, body = api.handle_alerts_latest(ctx)
    assert code == 200
    assert body["alert"]["ts"] == 2000
    assert body["alert"]["payload"]["kind"] == "fan_pct_high"


def test_ignores_non_alert_events(ctx):
    ctx["storage"].record_event("profile_switch", {"to": "boost"})
    code, body = api.handle_alerts_latest(ctx)
    assert body["alert"] is None
