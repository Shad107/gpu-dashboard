"""Tests for the auto-profile-switch logic — pure functions only.

The classify_load function inspects recent samples and decides which
power profile should be active : silent (idle), sweet (inference), boost
(sustained training).
"""
from __future__ import annotations

import pytest

from gpu_dashboard.modules.auto_profile import classify_load


def _samples(util_values, power_values=None):
    """Build a fake sample list with the given util_gpu values."""
    if power_values is None:
        power_values = [u * 3 for u in util_values]  # rough power ~ 3*util W
    return [
        {"ts": f"00:00:{i:02d}", "util_gpu": u, "power": p}
        for i, (u, p) in enumerate(zip(util_values, power_values))
    ]


class TestClassifyLoad:
    def test_idle_when_all_util_low(self):
        # 1 minute of <5% util → idle
        s = _samples([2, 0, 1, 3, 0, 2, 1, 0, 0, 2, 1, 0])
        assert classify_load(s) == "silent"

    def test_inference_when_bursty(self):
        # Mixed : some peaks but most low → inference-like
        s = _samples([2, 5, 80, 60, 10, 5, 2, 65, 70, 3, 2, 5])
        assert classify_load(s) == "sweet"

    def test_boost_when_sustained_high(self):
        # 90%+ util for the whole window → training
        s = _samples([90, 95, 98, 99, 95, 92, 95, 98, 99, 95, 96, 97])
        assert classify_load(s) == "boost"

    def test_empty_returns_silent(self):
        assert classify_load([]) == "silent"

    def test_single_sample_uses_it(self):
        s = _samples([95])
        assert classify_load(s) == "boost"
        s2 = _samples([5])
        assert classify_load(s2) == "silent"

    def test_thresholds_configurable(self):
        s = _samples([20, 25, 22, 28])  # average ~24
        # Custom thresholds: idle < 30 → silent
        assert classify_load(s, idle_threshold=30, boost_threshold=80) == "silent"
        # Custom: idle < 10 → with avg 24 should be sweet
        assert classify_load(s, idle_threshold=10, boost_threshold=80) == "sweet"

    def test_min_samples_to_decide(self):
        """If we don't have enough samples, return None (no decision)."""
        s = _samples([95])
        assert classify_load(s, min_samples=3) is None
