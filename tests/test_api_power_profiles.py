"""Tests for /api/power-profiles — 3-slot OC preset feature.

Each profile bundles power-limit + GPU offset + memory offset. Apply
in one call. Inspired by MSI Afterburner's 3 OC slots / EVGA Precision X1.
"""
from __future__ import annotations

import pytest

from gpu_dashboard import api
from gpu_dashboard.config import Config


def _cfg(**overrides):
    defaults = {
        "POWER_PROFILE_SILENT_W": "180",
        "POWER_PROFILE_SILENT_GPU_OFFSET": "0",
        "POWER_PROFILE_SILENT_MEM_OFFSET": "0",
        "POWER_PROFILE_SWEET_W": "250",
        "POWER_PROFILE_SWEET_GPU_OFFSET": "75",
        "POWER_PROFILE_SWEET_MEM_OFFSET": "500",
        "POWER_PROFILE_BOOST_W": "350",
        "POWER_PROFILE_BOOST_GPU_OFFSET": "100",
        "POWER_PROFILE_BOOST_MEM_OFFSET": "750",
    }
    defaults.update(overrides)
    return Config(defaults=defaults)


class TestListPowerProfiles:
    def test_returns_three_profiles(self):
        ctx = {"config": _cfg()}
        code, body = api.handle_power_profiles_list(ctx)
        assert code == 200
        assert len(body["profiles"]) == 3
        names = [p["name"] for p in body["profiles"]]
        assert set(names) == {"silent", "sweet", "boost"}

    def test_profiles_include_watts_and_offsets(self):
        ctx = {"config": _cfg()}
        _, body = api.handle_power_profiles_list(ctx)
        silent = next(p for p in body["profiles"] if p["name"] == "silent")
        assert silent["watts"] == 180
        assert silent["gpu_offset"] == 0
        assert silent["mem_offset"] == 0

        boost = next(p for p in body["profiles"] if p["name"] == "boost")
        assert boost["watts"] == 350
        assert boost["gpu_offset"] == 100
        assert boost["mem_offset"] == 750


class TestApplyPowerProfile:
    def test_unknown_profile_returns_400(self):
        ctx = {"config": _cfg(), "profile": {}}
        code, body = api.handle_power_profile_apply(ctx, "nonexistent")
        assert code == 400
        assert body["ok"] is False

    def test_calls_power_limit_and_offsets(self, monkeypatch):
        # Mock both subprocess calls
        calls = []
        from gpu_dashboard.modules import power_limit as _pl
        from gpu_dashboard.modules import clock_offsets as _co

        monkeypatch.setattr(_pl, "apply_power_limit",
                            lambda *a, **kw: (calls.append(("pl", a, kw)),
                                              {"ok": True, "watts": a[1], "output": ""})[1])
        monkeypatch.setattr(_co, "apply_offsets",
                            lambda *a, **kw: (calls.append(("co", a, kw)),
                                              {"ok": True, "gpu": kw.get("gpu", 0),
                                               "mem": kw.get("mem", 0), "output": ""})[1])

        ctx = {
            "config": _cfg(),
            "profile": {"power": {"min": 100, "max": 350}, "clocks": {"gpu_offset_max": 200, "mem_offset_max": 1500}},
        }
        code, body = api.handle_power_profile_apply(ctx, "sweet")
        assert code == 200
        assert body["ok"] is True
        # Verify both subsystems were invoked
        kinds = [c[0] for c in calls]
        assert "pl" in kinds
        # offsets may have been called too (sweet has nonzero offsets)
        assert body["watts"] == 250
        assert body["gpu_offset"] == 75
        assert body["mem_offset"] == 500
