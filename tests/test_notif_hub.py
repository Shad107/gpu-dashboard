"""R&D #6.1 — Unified notification hub tests."""
import json
import os
import tempfile
from unittest.mock import patch
from gpu_dashboard.modules import notif_hub


def _with_tmp_channels(td):
    return patch.object(notif_hub, "channels_path", return_value=os.path.join(td, "ch.json"))


def test_load_returns_empty_when_no_file():
    with tempfile.TemporaryDirectory() as td, _with_tmp_channels(td):
        assert notif_hub.load_channels() == []


def test_save_then_load_roundtrip():
    with tempfile.TemporaryDirectory() as td, _with_tmp_channels(td):
        channels = [{"id": "d1", "type": "discord", "url": "http://x", "enabled": True}]
        notif_hub.save_channels(channels)
        loaded = notif_hub.load_channels()
        assert loaded == channels


# ── filter logic ─────────────────────────────────────────────────────────


def test_disabled_channel_rejected():
    ch = {"enabled": False, "min_level": "info"}
    assert notif_hub.channel_accepts(ch, "critical") is False


def test_min_level_below_rejected():
    ch = {"enabled": True, "min_level": "warning"}
    assert notif_hub.channel_accepts(ch, "info") is False


def test_min_level_at_or_above_accepted():
    ch = {"enabled": True, "min_level": "warning"}
    assert notif_hub.channel_accepts(ch, "warning") is True
    assert notif_hub.channel_accepts(ch, "critical") is True


def test_gpu_filter_match_required():
    ch = {"enabled": True, "min_level": "info", "gpu_filter": 1}
    assert notif_hub.channel_accepts(ch, "info", gpu_index=0) is False
    assert notif_hub.channel_accepts(ch, "info", gpu_index=1) is True
    # gpu_index=None passes through (no filter on caller side)
    assert notif_hub.channel_accepts(ch, "info", gpu_index=None) is True


def test_quiet_hours_simple_window():
    # 14:00 to 16:00 quiet
    ch = {"enabled": True, "min_level": "info", "quiet_hours": [14, 16]}
    assert notif_hub.channel_accepts(ch, "info", now_hour=15) is False
    assert notif_hub.channel_accepts(ch, "info", now_hour=17) is True


def test_quiet_hours_wrap_midnight():
    # 22:00 to 07:00 quiet (overnight)
    ch = {"enabled": True, "min_level": "info", "quiet_hours": [22, 7]}
    assert notif_hub.channel_accepts(ch, "info", now_hour=23) is False
    assert notif_hub.channel_accepts(ch, "info", now_hour=3) is False
    assert notif_hub.channel_accepts(ch, "info", now_hour=10) is True


# ── adapter shape tests (with mocked HTTP) ───────────────────────────────


def test_discord_adapter_calls_post_json():
    ch = {"id": "d1", "type": "discord", "url": "http://disc"}
    with patch.object(notif_hub, "_post_json", return_value=(True, "HTTP 204")):
        ok, msg = notif_hub.send_discord(ch, "T", "B")
    assert ok is True


def test_discord_missing_url():
    ok, msg = notif_hub.send_discord({"id": "d1", "type": "discord"}, "T", "B")
    assert ok is False
    assert "missing" in msg


def test_slack_payload_has_text_key():
    ch = {"url": "http://slk"}
    captured = []
    def fake_post(url, payload, headers=None, timeout=5.0):
        captured.append(payload)
        return True, "HTTP 200"
    with patch.object(notif_hub, "_post_json", side_effect=fake_post):
        notif_hub.send_slack(ch, "Hello", "World")
    assert "text" in captured[0]
    assert "Hello" in captured[0]["text"]
    assert "World" in captured[0]["text"]


def test_gotify_appends_token_to_url():
    ch = {"url": "http://gotify.local/", "token": "abc123"}
    captured = []
    def fake_post(url, payload, headers=None, timeout=5.0):
        captured.append(url)
        return True, "HTTP 200"
    with patch.object(notif_hub, "_post_json", side_effect=fake_post):
        notif_hub.send_gotify(ch, "T", "B")
    assert captured[0].endswith("?token=abc123")


# ── send() fanout ────────────────────────────────────────────────────────


def test_send_routes_to_matching_channels_only():
    channels = [
        {"id": "a", "type": "discord", "url": "http://a", "enabled": True, "min_level": "info"},
        {"id": "b", "type": "discord", "url": "http://b", "enabled": True, "min_level": "critical"},
        {"id": "c", "type": "discord", "url": "http://c", "enabled": False, "min_level": "info"},
    ]
    with patch.object(notif_hub, "_post_json", return_value=(True, "HTTP 200")):
        results = notif_hub.send("warning", "Title", "Body", channels=channels)
    ids = {r["channel_id"] for r in results}
    # 'a' accepts (min=info, level=warning), 'b' rejects (needs critical), 'c' disabled
    assert ids == {"a"}


def test_send_unknown_type_records_failure():
    channels = [
        {"id": "x", "type": "lolnope", "enabled": True, "min_level": "info"},
    ]
    results = notif_hub.send("info", "T", "B", channels=channels)
    assert len(results) == 1
    assert results[0]["ok"] is False
    assert "unknown type" in results[0]["msg"]
