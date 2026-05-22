"""Tests for the per-app profile triggers module (cycle 116)."""
import json
import os
from pathlib import Path

import pytest

from gpu_dashboard.modules import app_triggers


# ─── load_triggers ─────────────────────────────────────────────────────────


def test_load_returns_empty_when_no_file(tmp_path):
    path = tmp_path / "missing.json"
    assert app_triggers.load_triggers(str(path)) == {}


def test_load_returns_dict(tmp_path):
    path = tmp_path / "ok.json"
    path.write_text(json.dumps({"blender": "boost", "llama-server": "boost"}))
    result = app_triggers.load_triggers(str(path))
    assert result == {"blender": "boost", "llama-server": "boost"}


def test_load_handles_invalid_json(tmp_path):
    path = tmp_path / "bad.json"
    path.write_text("{not json")
    assert app_triggers.load_triggers(str(path)) == {}


def test_load_filters_non_string_entries(tmp_path):
    path = tmp_path / "mixed.json"
    path.write_text(json.dumps({"good": "boost", "bad": 42, "ok": "sweet"}))
    result = app_triggers.load_triggers(str(path))
    assert "good" in result and "ok" in result
    assert "bad" not in result


def test_load_returns_empty_for_array_root(tmp_path):
    path = tmp_path / "arr.json"
    path.write_text(json.dumps(["blender", "boost"]))
    assert app_triggers.load_triggers(str(path)) == {}


# ─── save_triggers ─────────────────────────────────────────────────────────


def test_save_creates_file(tmp_path):
    path = tmp_path / "subdir" / "triggers.json"
    app_triggers.save_triggers({"blender": "boost"}, str(path))
    assert path.exists()
    content = json.loads(path.read_text())
    assert content == {"blender": "boost"}


def test_save_then_load_roundtrip(tmp_path):
    path = tmp_path / "rt.json"
    original = {"blender": "boost", "cyberpunk": "boost", "steam": "sweet"}
    app_triggers.save_triggers(original, str(path))
    loaded = app_triggers.load_triggers(str(path))
    assert loaded == original


# ─── match_trigger ─────────────────────────────────────────────────────────


def test_match_no_triggers():
    assert app_triggers.match_trigger({"some-app"}, {}) is None


def test_match_no_running_apps():
    assert app_triggers.match_trigger(set(), {"blender": "boost"}) is None


def test_match_simple_hit():
    apps = {"blender"}
    triggers = {"blender": "boost"}
    assert app_triggers.match_trigger(apps, triggers) == "boost"


def test_match_case_insensitive():
    apps = {"Blender"}
    triggers = {"blender": "boost"}
    assert app_triggers.match_trigger(apps, triggers) == "boost"


def test_match_substring():
    """A trigger 'blender' should match 'blender-cycles' too."""
    apps = {"blender-cycles"}
    triggers = {"blender": "boost"}
    assert app_triggers.match_trigger(apps, triggers) == "boost"


def test_match_no_partial_app_to_trigger():
    """A trigger 'blender' should NOT match a process named 'blnd'."""
    apps = {"blnd", "firefox"}
    triggers = {"blender": "boost"}
    assert app_triggers.match_trigger(apps, triggers) is None


def test_match_highest_priority_wins():
    """boost > sweet > silent when multiple triggers match."""
    apps = {"blender", "steam-runtime"}
    triggers = {"blender": "boost", "steam": "sweet"}
    assert app_triggers.match_trigger(apps, triggers) == "boost"

    # Reverse mapping shouldn't change the outcome
    triggers2 = {"steam": "sweet", "blender": "boost"}
    assert app_triggers.match_trigger(apps, triggers2) == "boost"


def test_match_returns_none_for_unknown_profile():
    """Unknown profile names still match — but get priority 0 (lowest)."""
    apps = {"blender", "cyberpunk"}
    triggers = {"blender": "weird-profile", "cyberpunk": "boost"}
    assert app_triggers.match_trigger(apps, triggers) == "boost"


# ─── scan_running_apps ─────────────────────────────────────────────────────


def test_scan_returns_set():
    """Sanity : returns a set (may be empty inside CI sandbox)."""
    apps = app_triggers.scan_running_apps()
    assert isinstance(apps, set)


def test_scan_handles_missing_proc(monkeypatch):
    """If /proc isn't accessible, returns empty set rather than crash."""
    def fail(*args, **kwargs):
        raise OSError("no /proc")
    monkeypatch.setattr(os, "listdir", fail)
    assert app_triggers.scan_running_apps() == set()
