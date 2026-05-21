"""Tests for gpu_dashboard.modules.fan_curve."""
from __future__ import annotations

import subprocess

import pytest

from gpu_dashboard.modules import fan_curve as fc


class TestInterpolate:
    """Pure function: temp °C → target fan % via piecewise linear interp."""

    def test_below_first_point_clamps(self):
        curve = [[30, 0], [60, 40], [80, 100]]
        assert fc.interpolate(curve, 20) == 0

    def test_above_last_point_clamps(self):
        curve = [[30, 0], [60, 40], [80, 100]]
        assert fc.interpolate(curve, 95) == 100

    def test_exact_point_returns_exact(self):
        curve = [[30, 0], [60, 40], [80, 100]]
        assert fc.interpolate(curve, 60) == 40

    def test_midpoint_linear(self):
        # Between (60, 40) and (80, 100): at 70 → midway → 70
        curve = [[60, 40], [80, 100]]
        assert fc.interpolate(curve, 70) == 70

    def test_quarter_linear(self):
        # 30 → 0, 60 → 40. At 45 (halfway) → 20
        curve = [[30, 0], [60, 40]]
        assert fc.interpolate(curve, 45) == 20

    def test_single_point_returns_that_value(self):
        curve = [[50, 60]]
        assert fc.interpolate(curve, 20) == 60
        assert fc.interpolate(curve, 80) == 60

    def test_empty_curve_returns_zero(self):
        assert fc.interpolate([], 50) == 0

    def test_unsorted_curve_is_sorted(self):
        curve = [[80, 100], [30, 0], [60, 40]]
        assert fc.interpolate(curve, 60) == 40


class TestValidateCurve:
    def test_valid_passes(self):
        fc.validate_curve([[30, 0], [60, 40], [80, 100]])

    def test_empty_raises(self):
        with pytest.raises(ValueError, match="empty"):
            fc.validate_curve([])

    def test_non_pairs_raise(self):
        with pytest.raises(ValueError):
            fc.validate_curve([[30, 0], [60]])  # missing fan%

    def test_temp_out_of_range_raises(self):
        with pytest.raises(ValueError):
            fc.validate_curve([[200, 50]])  # 200°C unreasonable

    def test_fan_out_of_range_raises(self):
        with pytest.raises(ValueError):
            fc.validate_curve([[40, 150]])  # 150% impossible

    def test_non_monotonic_temps_raise(self):
        with pytest.raises(ValueError, match="monotonic"):
            fc.validate_curve([[60, 40], [50, 30]])  # temps go down


class TestApplyFanCurve:
    def test_calls_nvidia_settings(self, monkeypatch):
        calls = []
        class R:
            returncode = 0
            stdout = ""
            stderr = ""
        def fake_run(cmd, *a, **kw):
            calls.append(cmd)
            return R()
        monkeypatch.setattr(subprocess, "run", fake_run)
        result = fc.apply_fan_speed(target_pct=65, display=":0", xauthority=None)
        assert result["ok"] is True
        # Should call nvidia-settings with the right attribute
        assert any("nvidia-settings" in str(c) for c in calls)
        # And include GPUFanControlState=1 + GPUTargetFanSpeed=65
        all_args = " ".join(" ".join(c) for c in calls)
        assert "GPUFanControlState=1" in all_args
        assert "65" in all_args

    def test_invalid_pct_raises(self):
        with pytest.raises(ValueError):
            fc.apply_fan_speed(target_pct=150, display=":0")


class TestPickCurveFromProfile:
    def test_uses_profile_default_curve(self):
        profile = {"fans": {"default_curve": [[30, 0], [70, 70]]}}
        curve = fc.pick_curve(profile)
        assert curve == [[30, 0], [70, 70]]

    def test_falls_back_to_default(self):
        profile = {}  # no fans key
        curve = fc.pick_curve(profile)
        assert len(curve) >= 2  # has a default

    def test_user_override_wins(self):
        profile = {"fans": {"default_curve": [[30, 0], [70, 70]]}}
        override = [[40, 20], [80, 100]]
        curve = fc.pick_curve(profile, override=override)
        assert curve == override
