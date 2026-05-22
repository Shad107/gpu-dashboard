"""Tests for /api/app-triggers GET + POST (cycle 118)."""
import json
import os

import pytest

from gpu_dashboard import api


@pytest.fixture
def home_tmp(tmp_path, monkeypatch):
    """Redirect ~/.config to a tmp dir so we don't trash the real user dir."""
    monkeypatch.setenv("HOME", str(tmp_path))
    return tmp_path


def test_get_returns_empty_when_no_config(home_tmp):
    code, body = api.handle_app_triggers_get({})
    assert code == 200
    assert body["ok"]
    assert body["triggers"] == {}


def test_get_returns_existing_triggers(home_tmp):
    cfg = home_tmp / ".config" / "gpu-dashboard"
    cfg.mkdir(parents=True)
    (cfg / "app_triggers.json").write_text(
        json.dumps({"blender": "boost", "steam-runtime": "sweet"})
    )
    code, body = api.handle_app_triggers_get({})
    assert body["triggers"] == {"blender": "boost", "steam-runtime": "sweet"}


def test_post_persists_triggers(home_tmp):
    payload = {"triggers": {"llama-server": "boost", "Blender": "boost"}}
    code, body = api.handle_app_triggers_post({}, payload)
    assert code == 200
    assert body["ok"]
    # Verify written to disk
    cfg = home_tmp / ".config" / "gpu-dashboard" / "app_triggers.json"
    assert cfg.exists()
    on_disk = json.loads(cfg.read_text())
    assert on_disk == {"llama-server": "boost", "Blender": "boost"}


def test_post_rejects_invalid_profile(home_tmp):
    payload = {"triggers": {"blender": "ultra-boost"}}
    code, body = api.handle_app_triggers_post({}, payload)
    assert code == 400
    assert "invalid" in body["error"].lower()


def test_post_rejects_non_dict_root(home_tmp):
    code, body = api.handle_app_triggers_post({}, ["array"])
    assert code == 400


def test_post_rejects_non_dict_triggers(home_tmp):
    code, body = api.handle_app_triggers_post({}, {"triggers": "string"})
    assert code == 400


def test_post_silently_drops_empty_keys(home_tmp):
    payload = {"triggers": {"": "boost", "   ": "sweet", "valid": "silent"}}
    code, body = api.handle_app_triggers_post({}, payload)
    assert code == 200
    assert body["triggers"] == {"valid": "silent"}


def test_post_then_get_roundtrip(home_tmp):
    api.handle_app_triggers_post({}, {"triggers": {"blender": "boost"}})
    code, body = api.handle_app_triggers_get({})
    assert body["triggers"] == {"blender": "boost"}
