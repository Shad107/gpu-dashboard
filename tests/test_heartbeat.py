"""R&D #6.2 — Deadman heartbeat tests."""
import json
import os
import tempfile
import time
from unittest.mock import patch
from gpu_dashboard import api


def _ctx():
    from gpu_dashboard.config import Config
    return {"config": Config(defaults={})}


def _with_tmp_heartbeats(td):
    return patch.object(api.alerts, "_heartbeats_path", return_value=os.path.join(td, "hb.json"))


def test_empty_list():
    with tempfile.TemporaryDirectory() as td, _with_tmp_heartbeats(td):
        code, body = api.handle_heartbeat_list(_ctx())
    assert code == 200
    assert body["heartbeats"] == []


def test_config_creates_token():
    with tempfile.TemporaryDirectory() as td, _with_tmp_heartbeats(td):
        code, body = api.handle_heartbeat_config(
            _ctx(), {"token": "train-job-1", "name": "Training loop", "interval_s": 600, "grace_s": 60}
        )
        assert code == 200
        assert body["token"] == "train-job-1"
        # Now the list contains it
        _, l = api.handle_heartbeat_list(_ctx())
        assert len(l["heartbeats"]) == 1
        assert l["heartbeats"][0]["status"] == "never"


def test_invalid_token_rejected():
    with tempfile.TemporaryDirectory() as td, _with_tmp_heartbeats(td):
        code, body = api.handle_heartbeat_config(_ctx(), {"token": "with spaces"})
        assert code == 400
        assert "alphanumeric" in body["error"]


def test_interval_out_of_range():
    with tempfile.TemporaryDirectory() as td, _with_tmp_heartbeats(td):
        code, body = api.handle_heartbeat_config(_ctx(), {"token": "x", "interval_s": 10})
        assert code == 400
        assert "out of range" in body["error"]


def test_ping_unknown_token_returns_404():
    with tempfile.TemporaryDirectory() as td, _with_tmp_heartbeats(td):
        code, body = api.handle_heartbeat_ping(_ctx(), "nonexistent")
    assert code == 404


def test_ping_known_token_records_timestamp():
    with tempfile.TemporaryDirectory() as td, _with_tmp_heartbeats(td):
        api.handle_heartbeat_config(_ctx(), {"token": "job1", "interval_s": 600})
        before = int(time.time())
        code, body = api.handle_heartbeat_ping(_ctx(), "job1")
        after = int(time.time())
        assert code == 200
        assert before <= body["ts"] <= after
        _, l = api.handle_heartbeat_list(_ctx())
        assert l["heartbeats"][0]["status"] == "ok"
        assert l["heartbeats"][0]["age_s"] < 5


def test_late_status_when_past_interval_plus_grace():
    with tempfile.TemporaryDirectory() as td, _with_tmp_heartbeats(td):
        api.handle_heartbeat_config(_ctx(), {"token": "job1", "interval_s": 60, "grace_s": 30})
        # Manually write a stale last_seen_ts
        data = api._load_heartbeats()
        data["tokens"]["job1"]["last_seen_ts"] = int(time.time()) - 200
        api._save_heartbeats(data)
        _, l = api.handle_heartbeat_list(_ctx())
        assert l["heartbeats"][0]["status"] == "late"


def test_delete_token():
    with tempfile.TemporaryDirectory() as td, _with_tmp_heartbeats(td):
        api.handle_heartbeat_config(_ctx(), {"token": "job1", "interval_s": 600})
        api.handle_heartbeat_config(_ctx(), {"delete": "job1"})
        _, l = api.handle_heartbeat_list(_ctx())
        assert l["heartbeats"] == []


def test_edit_preserves_last_seen():
    with tempfile.TemporaryDirectory() as td, _with_tmp_heartbeats(td):
        api.handle_heartbeat_config(_ctx(), {"token": "job1", "interval_s": 600})
        api.handle_heartbeat_ping(_ctx(), "job1")
        _, l1 = api.handle_heartbeat_list(_ctx())
        ts_before = l1["heartbeats"][0]["last_seen_ts"]
        # Edit name + interval but keep token
        api.handle_heartbeat_config(_ctx(), {"token": "job1", "name": "renamed", "interval_s": 1200})
        _, l2 = api.handle_heartbeat_list(_ctx())
        assert l2["heartbeats"][0]["name"] == "renamed"
        assert l2["heartbeats"][0]["last_seen_ts"] == ts_before
        assert l2["heartbeats"][0]["interval_s"] == 1200
