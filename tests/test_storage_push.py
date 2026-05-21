"""Tests for push_subscriptions storage operations."""
import pytest
from gpu_dashboard.storage import Storage


@pytest.fixture
def storage(tmp_path):
    s = Storage(str(tmp_path / "metrics.db"))
    yield s
    s.close()


def test_add_and_list(storage):
    storage.add_push_subscription("https://fcm/abc", "pub-123", "auth-456")
    rows = storage.list_push_subscriptions()
    assert len(rows) == 1
    assert rows[0]["endpoint"] == "https://fcm/abc"
    assert rows[0]["p256dh"] == "pub-123"
    assert rows[0]["auth"] == "auth-456"
    assert rows[0]["created_ts"] > 0


def test_add_is_idempotent_on_endpoint(storage):
    storage.add_push_subscription("https://fcm/abc", "pub-1", "auth-1")
    storage.add_push_subscription("https://fcm/abc", "pub-2", "auth-2")  # same endpoint
    rows = storage.list_push_subscriptions()
    assert len(rows) == 1
    # Latest values win (REPLACE)
    assert rows[0]["p256dh"] == "pub-2"


def test_multiple_endpoints(storage):
    storage.add_push_subscription("https://fcm/a", "p1", "a1")
    storage.add_push_subscription("https://mozilla/b", "p2", "a2")
    rows = storage.list_push_subscriptions()
    assert len(rows) == 2


def test_remove(storage):
    storage.add_push_subscription("https://fcm/a", "p1", "a1")
    storage.add_push_subscription("https://mozilla/b", "p2", "a2")
    n = storage.remove_push_subscription("https://fcm/a")
    assert n == 1
    rows = storage.list_push_subscriptions()
    assert len(rows) == 1
    assert rows[0]["endpoint"] == "https://mozilla/b"


def test_remove_missing(storage):
    n = storage.remove_push_subscription("https://nonexistent/endpoint")
    assert n == 0


def test_schema_version_at_least_3(storage):
    """push_subscriptions table introduced in v3 ; future migrations preserve it."""
    assert storage.schema_version() >= 3
