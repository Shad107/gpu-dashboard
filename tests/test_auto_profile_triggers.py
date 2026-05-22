"""Tests for app_triggers integration in AutoProfileDaemon (cycle 117)."""
import json
from unittest.mock import MagicMock

import pytest

from gpu_dashboard.modules import app_triggers
from gpu_dashboard.modules.auto_profile import AutoProfileDaemon


class FakeSampler:
    def __init__(self, samples=None):
        self.interval = 5
        self._samples = samples or []
    def snapshot(self):
        return list(self._samples)


def _high_load_samples(n=20):
    """Generate samples that classify_load() would call 'boost' (high util)."""
    return [{"util_gpu": 92, "power": 250} for _ in range(n)]


def _idle_load_samples(n=20):
    return [{"util_gpu": 1, "power": 10} for _ in range(n)]


def test_no_triggers_falls_back_to_classify(tmp_path, monkeypatch):
    """When triggers file is missing, the daemon behaves as before."""
    applied = []
    daemon = AutoProfileDaemon(
        sampler=FakeSampler(_high_load_samples()),
        api_apply_callback=lambda p: applied.append(p),
        min_stable_seconds=0,  # apply immediately
        app_triggers_path=str(tmp_path / "missing.json"),
    )
    # Run two ticks so the stability gate passes
    daemon._tick()
    daemon._tick()
    assert applied == ["boost"]


def test_trigger_overrides_classify(tmp_path, monkeypatch):
    """If a trigger app is running, its profile wins over load classification."""
    triggers_path = tmp_path / "triggers.json"
    triggers_path.write_text(json.dumps({"blender": "boost"}))

    # Force scan_running_apps to return blender, regardless of real /proc
    monkeypatch.setattr(app_triggers, "scan_running_apps", lambda: {"blender"})

    applied = []
    daemon = AutoProfileDaemon(
        sampler=FakeSampler(_idle_load_samples()),  # load would say silent
        api_apply_callback=lambda p: applied.append(p),
        min_stable_seconds=0,
        app_triggers_path=str(triggers_path),
    )
    daemon._tick()
    # Trigger override applied immediately (no stability gate)
    assert applied == ["boost"]
    assert daemon._current_classification == "boost"
    assert daemon._last_trigger_match == "blender"


def test_trigger_disappears_reverts_to_classify(tmp_path, monkeypatch):
    """When the trigger app stops running, the daemon goes back to load-based."""
    triggers_path = tmp_path / "triggers.json"
    triggers_path.write_text(json.dumps({"blender": "boost"}))

    # First tick : blender running → boost
    monkeypatch.setattr(app_triggers, "scan_running_apps", lambda: {"blender"})
    applied = []
    daemon = AutoProfileDaemon(
        sampler=FakeSampler(_idle_load_samples()),
        api_apply_callback=lambda p: applied.append(p),
        min_stable_seconds=0,
        app_triggers_path=str(triggers_path),
    )
    daemon._tick()
    assert applied == ["boost"]
    assert daemon._last_trigger_match == "blender"

    # Second tick : blender gone, low load → falls through to classify → silent
    monkeypatch.setattr(app_triggers, "scan_running_apps", lambda: set())
    daemon._tick()  # first call sets _classification_since but doesn't apply
    daemon._tick()  # second call sees stability, applies silent
    assert applied[-1] == "silent"
    assert daemon._last_trigger_match is None


def test_status_includes_trigger_match(tmp_path, monkeypatch):
    triggers_path = tmp_path / "triggers.json"
    triggers_path.write_text(json.dumps({"llama-server": "boost"}))
    monkeypatch.setattr(app_triggers, "scan_running_apps", lambda: {"llama-server"})

    daemon = AutoProfileDaemon(
        sampler=FakeSampler(_idle_load_samples()),
        api_apply_callback=lambda p: None,
        min_stable_seconds=0,
        app_triggers_path=str(triggers_path),
    )
    daemon._tick()
    s = daemon.status()
    assert s["trigger_match"] == "llama-server"
    assert s["current_classification"] == "boost"


def test_trigger_doesnt_reapply_if_already_active(tmp_path, monkeypatch):
    """If we've already applied 'boost' because of a trigger, don't keep re-applying."""
    triggers_path = tmp_path / "triggers.json"
    triggers_path.write_text(json.dumps({"blender": "boost"}))
    monkeypatch.setattr(app_triggers, "scan_running_apps", lambda: {"blender"})

    applied = []
    daemon = AutoProfileDaemon(
        sampler=FakeSampler(_idle_load_samples()),
        api_apply_callback=lambda p: applied.append(p),
        min_stable_seconds=0,
        app_triggers_path=str(triggers_path),
    )
    daemon._tick()
    daemon._tick()
    daemon._tick()
    # Apply called exactly once
    assert applied == ["boost"]
