"""R&D #13.6 — hot-GPU wizard tests."""
import json
import os
import tempfile
import time
import pytest
from unittest.mock import patch, mock_open
from gpu_dashboard.modules import hot_gpu_wizard as wiz


# ── step_ambient ─────────────────────────────────────────────────────────


def test_step_ambient_no_sensor_returns_skip():
    with patch.object(wiz, "_read_hwmon_ambient", return_value=None):
        s = wiz.step_ambient(gpu_temp_c=50)
    assert s["kind"] == "skip"


def test_step_ambient_hot_room_warns():
    with patch.object(wiz, "_read_hwmon_ambient", return_value=32):
        s = wiz.step_ambient(gpu_temp_c=70)
    assert s["kind"] == "warn"
    assert s["ambient_c"] == 32
    assert "ambient" in s["detail"].lower()


def test_step_ambient_normal_room_passes():
    with patch.object(wiz, "_read_hwmon_ambient", return_value=22):
        s = wiz.step_ambient(gpu_temp_c=50)
    assert s["kind"] == "pass"


# ── step_fan_curve ───────────────────────────────────────────────────────


def test_step_fan_curve_no_state_returns_skip():
    s = wiz.step_fan_curve(None, gpu_temp_c=50)
    assert s["kind"] == "skip"


def test_step_fan_curve_matches_curve_passes():
    curve = [{"temp": 40, "fan_pct": 30}, {"temp": 80, "fan_pct": 100}]
    # At temp 60 → expected 65%, measured 65% → pass
    s = wiz.step_fan_curve({"pct": 65, "rpm": 1500}, gpu_temp_c=60, profile_curve=curve)
    assert s["kind"] == "pass"


def test_step_fan_curve_deficit_warns_or_fails():
    curve = [{"temp": 40, "fan_pct": 30}, {"temp": 80, "fan_pct": 100}]
    # At 60°C expected 65% ; measured 30% → big deficit → fail
    s = wiz.step_fan_curve({"pct": 30, "rpm": 800}, gpu_temp_c=60, profile_curve=curve)
    assert s["kind"] == "fail"
    assert "deficit" in s["detail"].lower()


def test_step_fan_curve_above_range_pins_endpoint():
    curve = [{"temp": 40, "fan_pct": 30}, {"temp": 80, "fan_pct": 100}]
    s = wiz.step_fan_curve({"pct": 100, "rpm": 2000}, gpu_temp_c=90, profile_curve=curve)
    assert s["kind"] == "pass"


def test_step_fan_curve_no_curve_skips():
    s = wiz.step_fan_curve({"pct": 50}, gpu_temp_c=60, profile_curve=None)
    assert s["kind"] == "skip"


# ── step_dust_suspect ────────────────────────────────────────────────────


def _samples(temp_per_watt: float, n: int = 50):
    return [{"temp": 100 * temp_per_watt, "power": 100} for _ in range(n)]


def test_step_dust_first_run_saves_baseline():
    with tempfile.TemporaryDirectory() as td, \
         patch.object(wiz, "_baseline_path",
                      return_value=os.path.join(td, "baseline.json")):
        s = wiz.step_dust_suspect(_samples(0.3))
    assert s["kind"] == "pass"
    assert "baseline saved" in s["detail"]


def test_step_dust_under_threshold_passes():
    with tempfile.TemporaryDirectory() as td:
        baseline_p = os.path.join(td, "baseline.json")
        with open(baseline_p, "w") as f:
            json.dump({"ratio": 0.3, "ts": int(time.time())}, f)
        with patch.object(wiz, "_baseline_path", return_value=baseline_p):
            s = wiz.step_dust_suspect(_samples(0.31))  # +3%
    assert s["kind"] == "pass"


def test_step_dust_significant_drift_warns():
    with tempfile.TemporaryDirectory() as td:
        baseline_p = os.path.join(td, "baseline.json")
        with open(baseline_p, "w") as f:
            json.dump({"ratio": 0.3, "ts": int(time.time())}, f)
        with patch.object(wiz, "_baseline_path", return_value=baseline_p):
            s = wiz.step_dust_suspect(_samples(0.36))  # +20%
    assert s["kind"] == "warn"
    assert "drifted" in s["detail"].lower()


def test_step_dust_extreme_drift_fails():
    with tempfile.TemporaryDirectory() as td:
        baseline_p = os.path.join(td, "baseline.json")
        with open(baseline_p, "w") as f:
            json.dump({"ratio": 0.3, "ts": int(time.time())}, f)
        with patch.object(wiz, "_baseline_path", return_value=baseline_p):
            s = wiz.step_dust_suspect(_samples(0.40))  # +33%
    assert s["kind"] == "fail"


def test_step_dust_skips_when_too_few_samples():
    s = wiz.step_dust_suspect([])
    assert s["kind"] == "skip"


# ── step_driver_age ──────────────────────────────────────────────────────


def test_step_driver_age_no_history_skips():
    s = wiz.step_driver_age(None)
    assert s["kind"] == "skip"


def test_step_driver_age_very_recent_warns():
    last = {"ts": int(time.time()) - 3600}  # 1h ago
    s = wiz.step_driver_age(last)
    assert s["kind"] == "warn"


def test_step_driver_age_normal_passes():
    last = {"ts": int(time.time()) - 30 * 86400}  # 30 days ago
    s = wiz.step_driver_age(last)
    assert s["kind"] == "pass"


def test_step_driver_age_too_old_warns():
    last = {"ts": int(time.time()) - 400 * 86400}  # 400 days ago
    s = wiz.step_driver_age(last)
    assert s["kind"] == "warn"


# ── step_throttle_history ────────────────────────────────────────────────


def test_step_throttle_none_skips():
    s = wiz.step_throttle_history(None)
    assert s["kind"] == "skip"


def test_step_throttle_zero_passes():
    s = wiz.step_throttle_history(0)
    assert s["kind"] == "pass"


def test_step_throttle_few_warns():
    s = wiz.step_throttle_history(10)
    assert s["kind"] == "warn"


def test_step_throttle_many_fails():
    s = wiz.step_throttle_history(60)
    assert s["kind"] == "fail"


# ── aggregate_verdict ────────────────────────────────────────────────────


def test_aggregate_picks_worst():
    steps = [
        {"kind": "pass"}, {"kind": "warn"}, {"kind": "fail"},
        {"kind": "pass"}, {"kind": "skip"},
    ]
    assert wiz.aggregate_verdict(steps) == "fail"


def test_aggregate_all_pass_returns_pass():
    steps = [{"kind": "pass"}, {"kind": "pass"}, {"kind": "skip"}]
    assert wiz.aggregate_verdict(steps) == "pass"


def test_aggregate_only_skips_returns_skip():
    steps = [{"kind": "skip"}, {"kind": "skip"}]
    assert wiz.aggregate_verdict(steps) == "skip"


# ── run() end-to-end ─────────────────────────────────────────────────────


def test_run_full_pipeline_with_all_inputs():
    curve = [{"temp": 40, "fan_pct": 30}, {"temp": 80, "fan_pct": 100}]
    with patch.object(wiz, "_read_hwmon_ambient", return_value=22):
        result = wiz.run(
            gpu_temp_c=60,
            fan_state={"pct": 65, "rpm": 1500},
            profile_curve=curve,
            samples_recent=None,  # → skip dust
            last_drift={"ts": int(time.time()) - 30 * 86400},
            throttle_count_1h=0,
        )
    assert result["ok"] is True
    assert len(result["steps"]) == 5
    assert result["verdict"] in ("pass", "warn", "fail", "skip")


def test_run_with_no_inputs_skips_all():
    with patch.object(wiz, "_read_hwmon_ambient", return_value=None):
        result = wiz.run()
    assert all(s["kind"] == "skip" for s in result["steps"])
    assert result["verdict"] == "skip"
