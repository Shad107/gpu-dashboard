"""R&D #11.6 — iCalendar feed tests."""
import json
import os
import time
import pytest
from gpu_dashboard.modules import ical_feed as ic
from gpu_dashboard.storage import Storage


@pytest.fixture
def storage(tmp_path):
    s = Storage(str(tmp_path / "test.db"))
    # Add a couple of fake alert events
    now = int(time.time())
    with s._lock:
        s._conn.execute(
            "INSERT INTO events (ts, kind, payload) VALUES (?, ?, ?)",
            (now - 3600, "alert.gpu_temp_high",
             json.dumps({"kind": "gpu_temp_high", "value": 88, "threshold": 85})),
        )
        s._conn.execute(
            "INSERT INTO events (ts, kind, payload) VALUES (?, ?, ?)",
            (now - 1800, "profile.boost",
             json.dumps({"to": "boost"})),
        )
    return s


# ── helpers ───────────────────────────────────────────────────────────────


def test_fold_short_line_passthrough():
    assert ic._fold("SHORT:line") == "SHORT:line"


def test_fold_long_line_breaks_at_75():
    long = "X" * 200
    folded = ic._fold(long)
    # First chunk = 75 chars, then "\r\n " + 74 chars, etc.
    assert "\r\n " in folded
    # First segment is exactly 75 chars
    first = folded.split("\r\n")[0]
    assert len(first) == 75


def test_escape_text_handles_specials():
    assert ic._escape_text("a,b") == "a\\,b"
    assert ic._escape_text("a;b") == "a\;b"
    assert ic._escape_text("a\nb") == "a\\nb"
    assert ic._escape_text("a\\b") == "a\\\\b"


def test_fmt_utc_format():
    # 2024-01-15 12:34:56 UTC → 20240115T123456Z
    ts = 1705321996
    assert ic._fmt_utc(ts) == "20240115T123456Z"


def test_event_uid_deterministic():
    uid1 = ic._event_uid("ALERT", 1000, "msg")
    uid2 = ic._event_uid("ALERT", 1000, "msg")
    assert uid1 == uid2
    # Different category → different uid
    assert ic._event_uid("DRIFT", 1000, "msg") != uid1


# ── make_event ────────────────────────────────────────────────────────────


def test_make_event_has_required_fields():
    lines = ic.make_event("ALERT", int(time.time()), "Test event", "details")
    text = "\n".join(lines)
    assert "BEGIN:VEVENT" in text
    assert "END:VEVENT" in text
    assert "UID:" in text
    assert "DTSTART:" in text
    assert "DTEND:" in text
    assert "SUMMARY:Test event" in text
    assert "DESCRIPTION:details" in text
    assert "CATEGORIES:ALERT" in text


def test_make_event_escapes_summary():
    lines = ic.make_event("X", 100, "comma, in, text")
    text = "\n".join(lines)
    assert "SUMMARY:comma\\, in\\, text" in text


# ── collect_events ────────────────────────────────────────────────────────


def test_collect_events_no_storage_returns_empty():
    assert ic.collect_events(None) == []


def test_collect_events_from_db(storage):
    events = ic.collect_events(storage, days=7)
    # 2 events seeded : one ALERT, one PROFILE
    cats = {e["category"] for e in events}
    assert "ALERT" in cats
    assert "PROFILE" in cats
    # Alert summary should include the metric
    alert = next(e for e in events if e["category"] == "ALERT")
    assert "gpu_temp_high" in alert["summary"]


def test_collect_events_respects_days_window(storage):
    """Events older than `days` are excluded."""
    # Insert an event 60 days ago
    with storage._lock:
        storage._conn.execute(
            "INSERT INTO events (ts, kind, payload) VALUES (?, ?, ?)",
            (int(time.time()) - 60 * 86400, "alert.old", "{}"),
        )
    events = ic.collect_events(storage, days=30)
    # Old event must NOT appear
    assert not any(e["summary"] == "GPU alert : old" for e in events)


# ── render_calendar ──────────────────────────────────────────────────────


def test_render_calendar_empty():
    text = ic.render_calendar([])
    assert "BEGIN:VCALENDAR" in text
    assert "END:VCALENDAR" in text
    assert "BEGIN:VEVENT" not in text


def test_render_calendar_with_events():
    events = [{
        "category": "ALERT", "ts": int(time.time()),
        "summary": "Temperature high", "description": "88°C > 85°C",
    }]
    text = ic.render_calendar(events)
    assert text.startswith("BEGIN:VCALENDAR")
    assert text.endswith("END:VCALENDAR\r\n")
    assert "BEGIN:VEVENT" in text
    assert "SUMMARY:Temperature high" in text


def test_render_calendar_includes_calname():
    text = ic.render_calendar([])
    assert "X-WR-CALNAME:GreenWatts" in text


def test_render_calendar_uses_crlf_line_endings():
    """RFC 5545 mandates CRLF line endings."""
    text = ic.render_calendar([{"category": "X", "ts": 100, "summary": "s"}])
    # Should contain \r\n separators
    assert "\r\n" in text


def test_render_calendar_drift_event_format():
    """Drift event description shows field old → new mapping."""
    events = [{
        "category": "DRIFT", "ts": int(time.time()),
        "summary": "Driver/kernel drift detected",
        "description": "driver_version: 535 → 560\nkernel_release: 6.5 → 6.6",
    }]
    text = ic.render_calendar(events)
    # \n in description gets escaped to \\n per RFC 5545
    assert "driver_version" in text
    assert "560" in text
