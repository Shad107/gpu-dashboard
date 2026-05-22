"""R&D #4.5 — Idle-state audit tests."""
import subprocess
from unittest.mock import patch
from gpu_dashboard import api
from gpu_dashboard.config import Config


def _ctx():
    return {"config": Config(defaults={})}


class FakeProc:
    def __init__(self, stdout="", returncode=0):
        self.stdout = stdout
        self.returncode = returncode


def test_no_nvidia_smi_returns_available_false():
    with patch.object(subprocess, "run", side_effect=FileNotFoundError):
        code, body = api.handle_idle_audit(_ctx())
    assert code == 200
    assert body["ok"] is True
    assert body["available"] is False


def test_idle_within_baseline_returns_ok():
    out = "NVIDIA GeForce RTX 3090, 0, 18.5, P8, Enabled"
    with patch.object(subprocess, "run", return_value=FakeProc(stdout=out)):
        code, body = api.handle_idle_audit(_ctx())
    assert code == 200
    assert body["available"] is True
    assert body["status"] == "idle"
    assert body["verdict_kind"] == "ok"
    assert body["checklist"] == []
    assert body["baseline"]["low"] == 15
    assert body["baseline"]["high"] == 25


def test_idle_above_baseline_returns_high_with_checklist():
    # 35W on a 3090 at idle is way above 25W expected
    out = "NVIDIA GeForce RTX 3090, 0, 35.0, P0, Disabled"
    with patch.object(subprocess, "run", return_value=FakeProc(stdout=out)):
        code, body = api.handle_idle_audit(_ctx())
    assert code == 200
    assert body["status"] == "idle"
    assert body["verdict_kind"] == "high"
    keys = {item["key"] for item in body["checklist"]}
    # Disabled persistence + non-P8 pstate should both flag
    assert "persistence_mode" in keys
    assert "pstate_high" in keys
    # Generic items always present on 'high'
    assert "compositor" in keys
    assert "modeset" in keys


def test_busy_gpu_returns_busy_status():
    # util > 5 → not idle, can't audit yet
    out = "NVIDIA GeForce RTX 3090, 60, 200.0, P0, Enabled"
    with patch.object(subprocess, "run", return_value=FakeProc(stdout=out)):
        code, body = api.handle_idle_audit(_ctx())
    assert code == 200
    assert body["status"] == "busy"
    assert "busy" in body["verdict"].lower()


def test_unknown_gpu_family_returns_status_unknown():
    out = "Some Future GPU, 0, 12.0, P8, Enabled"
    with patch.object(subprocess, "run", return_value=FakeProc(stdout=out)):
        code, body = api.handle_idle_audit(_ctx())
    assert code == 200
    assert body["available"] is True
    assert body["status"] == "unknown"
    assert "no baseline" in body["verdict"].lower()


def test_4090_baseline_high_band():
    out = "NVIDIA GeForce RTX 4090, 0, 20.0, P8, Enabled"
    with patch.object(subprocess, "run", return_value=FakeProc(stdout=out)):
        code, body = api.handle_idle_audit(_ctx())
    assert code == 200
    assert body["baseline"]["low"] == 15
    assert body["baseline"]["high"] == 25
    assert "Ada/RTX 4090" in body["baseline"]["family"]
    assert body["verdict_kind"] == "ok"
