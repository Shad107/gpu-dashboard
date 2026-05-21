"""Tests for GET /api/version (cycle 109)."""
import pytest

from gpu_dashboard import api
from gpu_dashboard.config import Config
from gpu_dashboard.storage import Storage


@pytest.fixture
def ctx(tmp_path):
    storage = Storage(str(tmp_path / "metrics.db"))
    cfg = Config(defaults={
        "MODULE_POWER_LIMIT": "1",
        "MODULE_FAN_CURVE": "1",
        "MODULE_TELEGRAM_ALERTS": "0",
    })
    yield {"storage": storage, "config": cfg}
    storage.close()


def test_returns_ok_and_version(ctx):
    code, body = api.handle_version(ctx)
    assert code == 200
    assert body["ok"]
    assert "version" in body
    assert isinstance(body["version"], str)
    assert len(body["version"]) > 0


def test_returns_schema_version(ctx):
    _, body = api.handle_version(ctx)
    assert body["schema_version"] == 4  # current schema


def test_returns_modules_enabled_list(ctx):
    _, body = api.handle_version(ctx)
    assert "power_limit" in body["modules_enabled"]
    assert "fan_curve" in body["modules_enabled"]
    assert "telegram_alerts" not in body["modules_enabled"]


def test_works_without_storage():
    code, body = api.handle_version({"config": None})
    assert code == 200
    assert body["schema_version"] is None


def test_works_without_config(ctx):
    """If config missing, modules_enabled is empty rather than crashing."""
    ctx_no_cfg = {"storage": ctx["storage"]}
    code, body = api.handle_version(ctx_no_cfg)
    assert code == 200
    assert body["modules_enabled"] == []
