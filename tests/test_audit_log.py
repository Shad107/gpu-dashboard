"""R&D #9.6 — Multi-user audit log tests."""
import pytest
from gpu_dashboard import api
from gpu_dashboard.config import Config
from gpu_dashboard.storage import Storage


@pytest.fixture
def storage(tmp_path):
    return Storage(str(tmp_path / "test.db"))


@pytest.fixture
def ctx(storage):
    return {"config": Config(defaults={}), "storage": storage}


def test_storage_record_audit_returns_row_id(storage):
    rid = storage.record_audit("/api/test", before={"a": 1}, after={"a": 2})
    assert rid > 0


def test_storage_get_audit_log_returns_entries(storage):
    storage.record_audit("/api/fan", before={"pct": 30}, after={"pct": 60})
    storage.record_audit("/api/pl", before={"w": 250}, after={"w": 350})
    rows = storage.get_audit_log(limit=10)
    assert len(rows) == 2
    # Newest first
    assert rows[0]["route"] == "/api/pl"
    assert rows[1]["route"] == "/api/fan"


def test_storage_audit_records_actor(storage):
    storage.record_audit("/api/test", actor="user-shad")
    rows = storage.get_audit_log()
    assert rows[0]["actor"] == "user-shad"


def test_storage_audit_anonymous_when_no_actor(storage):
    storage.record_audit("/api/test")
    rows = storage.get_audit_log()
    assert rows[0]["actor"] == "anonymous"


def test_storage_audit_json_serializes_payloads(storage):
    storage.record_audit("/api/x", before={"deep": {"k": [1, 2]}})
    rows = storage.get_audit_log()
    import json
    decoded = json.loads(rows[0]["before_json"])
    assert decoded["deep"]["k"] == [1, 2]


def test_storage_audit_since_filter(storage):
    import time
    storage.record_audit("/api/old")
    time.sleep(1.1)  # ensure ts increments by 1s
    storage.record_audit("/api/new")
    cutoff = storage.get_audit_log()[0]["ts"]  # ts of /api/new
    only_new = storage.get_audit_log(since_ts=cutoff)
    assert len(only_new) == 1
    assert only_new[0]["route"] == "/api/new"


def test_handler_returns_503_without_storage():
    code, body = api.handle_audit_log({"config": Config(defaults={})})
    assert code == 503


def test_handler_returns_entries(ctx, storage):
    storage.record_audit("/api/fan", before={"x": 1}, after={"x": 2}, actor="alice")
    code, body = api.handle_audit_log(ctx, {})
    assert code == 200
    assert body["count"] == 1
    assert body["entries"][0]["actor"] == "alice"


def test_handler_limit_clamped(ctx, storage):
    # Insert 5 entries, ask for 3
    for i in range(5):
        storage.record_audit(f"/api/r{i}")
    code, body = api.handle_audit_log(ctx, {"limit": "3"})
    assert body["count"] == 3


def test_handler_invalid_limit_falls_back_to_default(ctx, storage):
    storage.record_audit("/api/r0")
    code, body = api.handle_audit_log(ctx, {"limit": "not-a-number"})
    assert code == 200
    assert body["count"] == 1
