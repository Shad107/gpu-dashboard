"""Tests for the fan curve persistence endpoint + validation."""
import json
import os

import pytest

from gpu_dashboard import api
from gpu_dashboard.modules import fan_curve


@pytest.fixture
def ctx(tmp_path, monkeypatch):
    """Isolate ~/.config/gpu-dashboard via HOME override."""
    monkeypatch.setenv("HOME", str(tmp_path))
    yield {"config": MockConfig(), "profile": None}


class MockConfig:
    def get_bool(self, key, default=False): return False
    def get_int(self, key, default=0): return default
    def get(self, key, default=""): return default


# ─── validate_curve ─────────────────────────────────────────────────────

def test_validate_accepts_valid_curve():
    ok, err = fan_curve.validate_curve([[30, 0], [50, 30], [70, 70], [90, 100]])
    assert ok
    assert err == ""


def test_validate_rejects_empty():
    ok, err = fan_curve.validate_curve([])
    assert not ok
    assert "at least 2" in err


def test_validate_rejects_single_point():
    ok, err = fan_curve.validate_curve([[50, 50]])
    assert not ok


def test_validate_rejects_out_of_range_temp():
    ok, err = fan_curve.validate_curve([[30, 50], [150, 100]])
    assert not ok
    assert "temp" in err.lower()


def test_validate_rejects_out_of_range_fan():
    ok, err = fan_curve.validate_curve([[30, 50], [70, 110]])
    assert not ok
    assert "fan" in err.lower()


def test_validate_rejects_unsorted():
    ok, err = fan_curve.validate_curve([[50, 30], [30, 0], [70, 70]])
    assert not ok
    assert "sorted" in err.lower()


def test_validate_rejects_duplicate_temps():
    ok, err = fan_curve.validate_curve([[50, 30], [50, 50]])
    assert not ok


def test_validate_rejects_non_int():
    ok, err = fan_curve.validate_curve([[30, 0], [50.5, 30]])
    assert not ok


# ─── POST /api/fan-curve ─────────────────────────────────────────────────

def test_post_accepts_valid_curve(ctx, tmp_path):
    code, body = api.handle_fan_curve_post(ctx, {"curve": [[30, 0], [70, 70], [85, 100]]})
    assert code == 200
    assert body["ok"]
    path = tmp_path / ".config" / "gpu-dashboard" / "fan_curve.json"
    assert path.exists()
    saved = json.loads(path.read_text())
    assert saved["curve"] == [[30, 0], [70, 70], [85, 100]]


def test_post_rejects_invalid_curve(ctx):
    code, body = api.handle_fan_curve_post(ctx, {"curve": [[150, 50]]})
    assert code == 400
    assert not body["ok"]


def test_post_rejects_missing_curve_field(ctx):
    code, body = api.handle_fan_curve_post(ctx, {})
    assert code == 400


# ─── pick_curve override file ────────────────────────────────────────────

def test_pick_curve_reads_override_file(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    cfg_dir = tmp_path / ".config" / "gpu-dashboard"
    cfg_dir.mkdir(parents=True)
    (cfg_dir / "fan_curve.json").write_text(json.dumps({
        "curve": [[20, 0], [60, 50], [80, 100]],
    }))
    c = fan_curve.pick_curve()
    assert c == [[20, 0], [60, 50], [80, 100]]


def test_pick_curve_falls_back_when_override_corrupted(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    cfg_dir = tmp_path / ".config" / "gpu-dashboard"
    cfg_dir.mkdir(parents=True)
    (cfg_dir / "fan_curve.json").write_text("{not valid json")
    c = fan_curve.pick_curve()
    # Should be the built-in default, not raise
    assert len(c) >= 2
