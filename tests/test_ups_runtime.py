"""R&D #20.7 — UPS runtime estimator tests."""
import pytest
from unittest.mock import patch
from gpu_dashboard.modules import ups_runtime as ur


# ── adjust_runtime ─────────────────────────────────────────────────────


def test_adjust_runtime_baseline_only():
    """gpu_w=0 → no adjustment beyond clamp."""
    assert ur.adjust_runtime(600, gpu_w=0, baseline_w=80,
                              ups_capacity_wh=600) == 600


def test_adjust_runtime_gpu_doubles_load():
    """If GPU draws as much as baseline, runtime should roughly halve."""
    r = ur.adjust_runtime(600, gpu_w=80, baseline_w=80,
                           ups_capacity_wh=600)
    assert 290 <= r <= 310


def test_adjust_runtime_heavy_gpu():
    """350 W GPU on 80 W baseline → runtime drops drastically."""
    r = ur.adjust_runtime(600, gpu_w=350, baseline_w=80,
                           ups_capacity_wh=600)
    assert r < 200  # was 10 min, now much less


def test_adjust_runtime_clamped_to_reported():
    """Never extends beyond reported_s."""
    r = ur.adjust_runtime(300, gpu_w=10, baseline_w=80,
                           ups_capacity_wh=10000)
    assert r <= 300


def test_adjust_runtime_zero_capacity_returns_reported():
    assert ur.adjust_runtime(300, gpu_w=100, baseline_w=80,
                              ups_capacity_wh=0) == 300


def test_adjust_runtime_negative_inputs_safe():
    assert ur.adjust_runtime(-5, gpu_w=10, baseline_w=80,
                              ups_capacity_wh=600) == 0


# ── classify ───────────────────────────────────────────────────────────


def test_classify_on_grid():
    v = ur.classify(on_battery=False, low_battery=False, runtime_s=600)
    assert v["verdict"] == "on_grid"


def test_classify_safe_when_plenty_runtime():
    v = ur.classify(on_battery=True, low_battery=False, runtime_s=3600)
    assert v["verdict"] == "safe"
    assert "continue" in v["reason"]


def test_classify_pause_when_under_5_min():
    v = ur.classify(on_battery=True, low_battery=False, runtime_s=240)
    assert v["verdict"] == "pause_jobs"


def test_classify_shutdown_when_low_battery_flag():
    v = ur.classify(on_battery=True, low_battery=True, runtime_s=600)
    assert v["verdict"] == "shutdown_now"
    assert "NOW" in v["reason"]


def test_classify_shutdown_when_runtime_none():
    v = ur.classify(on_battery=True, low_battery=False, runtime_s=None)
    assert v["verdict"] == "shutdown_now"


def test_classify_shutdown_when_safe_runtime_under_60s():
    v = ur.classify(on_battery=True, low_battery=False, runtime_s=30)
    assert v["verdict"] == "shutdown_now"


# ── gpu_total_power_w ──────────────────────────────────────────────────


def test_gpu_total_power_no_smi(monkeypatch):
    monkeypatch.setattr(ur.shutil, "which", lambda x: None)
    assert ur.gpu_total_power_w() is None


# ── status ─────────────────────────────────────────────────────────────


def test_status_on_grid_with_ups():
    with patch("gpu_dashboard.modules.ups_nut.query",
               return_value={"available": True, "on_battery": False,
                              "low_battery": False, "runtime_s": 1800}):
        with patch.object(ur, "gpu_total_power_w", return_value=100.0):
            s = ur.status()
    assert s["verdict"]["verdict"] == "on_grid"
    assert s["gpu_total_power_w"] == 100.0


def test_status_on_battery_pause_jobs():
    """On battery, 240s reported runtime, light GPU load → pause."""
    with patch("gpu_dashboard.modules.ups_nut.query",
               return_value={"available": True, "on_battery": True,
                              "low_battery": False, "runtime_s": 360}):
        with patch.object(ur, "gpu_total_power_w", return_value=20.0):
            s = ur.status()
    # adjusted_runtime: 360 at baseline; gpu adds 20% load → ~300s
    # safe = 90% → ~270s → pause_jobs (under 5 min threshold)
    assert s["verdict"]["verdict"] == "pause_jobs"


def test_status_ups_unavailable():
    with patch("gpu_dashboard.modules.ups_nut.query",
               return_value={"available": False}):
        with patch.object(ur, "gpu_total_power_w", return_value=200.0):
            s = ur.status()
    assert s["ups_available"] is False
    # No on_battery / low_battery flags → on_grid verdict
    assert s["verdict"]["verdict"] == "on_grid"


def test_status_uses_config_overrides():
    cfg = {"UPS_CAPACITY_WH": "1000", "UPS_BASELINE_W": "100",
           "UPS_SAFE_BUFFER_PCT": "20"}
    with patch("gpu_dashboard.modules.ups_nut.query",
               return_value={"available": False}):
        with patch.object(ur, "gpu_total_power_w", return_value=None):
            s = ur.status(cfg)
    assert s["capacity_wh"] == 1000.0
    assert s["baseline_w"] == 100.0
