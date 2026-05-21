"""Tests for the threshold-alerts pure function."""
from __future__ import annotations

import time

import pytest

from gpu_dashboard.modules.alert_monitor import check_thresholds, AlertState


def _sample(**fields):
    base = {"temp": 50, "fan_pct": 40, "power": 200, "mem_temp": None}
    base.update(fields)
    return base


class TestCheckThresholds:
    def test_below_thresholds_returns_no_alert(self):
        thresholds = {"gpu_temp": 85, "mem_temp": 95, "fan_pct": 95, "min_consecutive": 3}
        state = AlertState()
        samples = [_sample(temp=70, fan_pct=60) for _ in range(5)]
        alerts = check_thresholds(samples, thresholds, state)
        assert alerts == []

    def test_high_gpu_temp_3_consecutive_fires(self):
        thresholds = {"gpu_temp": 85, "mem_temp": 95, "fan_pct": 95, "min_consecutive": 3}
        state = AlertState()
        samples = [_sample(temp=t) for t in [60, 70, 86, 88, 87]]
        alerts = check_thresholds(samples, thresholds, state)
        # Last 3 are all > 85 → should fire
        assert len(alerts) == 1
        assert alerts[0]["kind"] == "gpu_temp_high"

    def test_only_2_consecutive_does_not_fire(self):
        thresholds = {"gpu_temp": 85, "mem_temp": 95, "fan_pct": 95, "min_consecutive": 3}
        state = AlertState()
        samples = [_sample(temp=t) for t in [60, 70, 80, 86, 87]]
        alerts = check_thresholds(samples, thresholds, state)
        assert alerts == []

    def test_mem_temp_high(self):
        thresholds = {"gpu_temp": 85, "mem_temp": 95, "fan_pct": 95, "min_consecutive": 3}
        state = AlertState()
        samples = [_sample(temp=70, mem_temp=mt) for mt in [80, 96, 97, 98]]
        alerts = check_thresholds(samples, thresholds, state)
        assert len(alerts) == 1
        assert alerts[0]["kind"] == "mem_temp_high"

    def test_mem_temp_none_does_not_fire(self):
        """If the card doesn't report mem_temp (None), no alert."""
        thresholds = {"gpu_temp": 85, "mem_temp": 95, "fan_pct": 95, "min_consecutive": 3}
        state = AlertState()
        samples = [_sample(temp=70, mem_temp=None) for _ in range(5)]
        alerts = check_thresholds(samples, thresholds, state)
        assert alerts == []

    def test_cooldown_prevents_duplicate(self):
        thresholds = {"gpu_temp": 85, "mem_temp": 95, "fan_pct": 95,
                      "min_consecutive": 3, "cooldown_seconds": 300}
        state = AlertState()
        samples = [_sample(temp=90) for _ in range(5)]
        # First call → fires
        alerts1 = check_thresholds(samples, thresholds, state)
        assert len(alerts1) == 1
        # Second call right after → no alert (cooldown)
        alerts2 = check_thresholds(samples, thresholds, state)
        assert alerts2 == []

    def test_cooldown_expires(self):
        thresholds = {"gpu_temp": 85, "mem_temp": 95, "fan_pct": 95,
                      "min_consecutive": 3, "cooldown_seconds": 1}
        state = AlertState()
        samples = [_sample(temp=90) for _ in range(5)]
        # First fire
        check_thresholds(samples, thresholds, state)
        # Simulate cooldown expiry
        state.last_fired["gpu_temp_high"] = time.time() - 2
        alerts = check_thresholds(samples, thresholds, state)
        assert len(alerts) == 1

    def test_multiple_simultaneous_alerts(self):
        thresholds = {"gpu_temp": 85, "mem_temp": 95, "fan_pct": 95, "min_consecutive": 3}
        state = AlertState()
        samples = [_sample(temp=90, mem_temp=98, fan_pct=97) for _ in range(3)]
        alerts = check_thresholds(samples, thresholds, state)
        kinds = {a["kind"] for a in alerts}
        assert "gpu_temp_high" in kinds
        assert "mem_temp_high" in kinds
        assert "fan_pct_high" in kinds

    def test_empty_samples(self):
        thresholds = {"gpu_temp": 85, "mem_temp": 95, "fan_pct": 95, "min_consecutive": 3}
        state = AlertState()
        assert check_thresholds([], thresholds, state) == []


class TestVramAlert:
    def test_vram_pct_high_fires(self):
        thresholds = {
            "vram_pct": 90, "min_consecutive": 3,
            "mem_total_mib": 24576,  # RTX 3090
        }
        state = AlertState()
        # 95%+ used = 23347+ MiB
        samples = [_sample(temp=50, mem_used_mib=23500) for _ in range(3)]
        alerts = check_thresholds(samples, thresholds, state)
        assert len(alerts) == 1
        assert alerts[0]["kind"] == "vram_pct_high"

    def test_vram_below_threshold_does_not_fire(self):
        thresholds = {"vram_pct": 90, "min_consecutive": 3, "mem_total_mib": 24576}
        state = AlertState()
        samples = [_sample(mem_used_mib=10000) for _ in range(3)]  # ~40%
        assert check_thresholds(samples, thresholds, state) == []

    def test_vram_requires_mem_total(self):
        """Without mem_total_mib in thresholds, VRAM alert cannot be computed."""
        thresholds = {"vram_pct": 90, "min_consecutive": 3}  # no mem_total_mib
        state = AlertState()
        samples = [_sample(mem_used_mib=23500) for _ in range(3)]
        alerts = check_thresholds(samples, thresholds, state)
        # No alert because we can't compute % without total
        assert all(a["kind"] != "vram_pct_high" for a in alerts)
