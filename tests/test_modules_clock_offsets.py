"""Tests pour gpu_dashboard.modules.clock_offsets.

Le module gère :
- Classification des valeurs d'offset dans les zones safe/moderate/aggressive/danger
- Validation contre les limites du profil
- Lecture des offsets actuels via nvidia-settings
- Application des offsets (no-sudo, juste nvidia-settings -a sur :0)
"""
from __future__ import annotations

import subprocess
import pytest

from gpu_dashboard.modules import clock_offsets as co


# ─────────────────────────────── fixtures ──────────────────────────────────


@pytest.fixture
def profile_3090():
    return {
        "clocks": {
            "gpu_offset_max": 200,
            "mem_offset_max": 1500,
            "gpu_zones": {"safe": 50, "moderate": 100, "aggressive": 150, "danger": 200},
            "mem_zones": {"safe": 300, "moderate": 700, "aggressive": 1200, "danger": 1500},
            "sweet_spot": {"gpu": 100, "mem": 500},
        }
    }


class _FakeCompleted:
    def __init__(self, stdout="", stderr="", returncode=0):
        self.stdout = stdout
        self.stderr = stderr
        self.returncode = returncode


# ───────────────────────────── classify_zone ───────────────────────────────


class TestClassifyZone:
    def test_safe_zone(self, profile_3090):
        zones = profile_3090["clocks"]["gpu_zones"]
        assert co.classify_zone(0, zones) == "safe"
        assert co.classify_zone(50, zones) == "safe"

    def test_moderate_zone(self, profile_3090):
        zones = profile_3090["clocks"]["gpu_zones"]
        assert co.classify_zone(75, zones) == "moderate"
        assert co.classify_zone(100, zones) == "moderate"

    def test_aggressive_zone(self, profile_3090):
        zones = profile_3090["clocks"]["gpu_zones"]
        assert co.classify_zone(125, zones) == "aggressive"
        assert co.classify_zone(150, zones) == "aggressive"

    def test_danger_zone(self, profile_3090):
        zones = profile_3090["clocks"]["gpu_zones"]
        assert co.classify_zone(175, zones) == "danger"
        assert co.classify_zone(200, zones) == "danger"

    def test_above_danger_returns_danger(self, profile_3090):
        zones = profile_3090["clocks"]["gpu_zones"]
        assert co.classify_zone(500, zones) == "danger"

    def test_negative_treated_as_safe(self, profile_3090):
        zones = profile_3090["clocks"]["gpu_zones"]
        assert co.classify_zone(-50, zones) == "safe"

    def test_empty_zones_returns_unknown(self):
        assert co.classify_zone(100, {}) == "unknown"


# ─────────────────────────── validate_offsets ──────────────────────────────


class TestValidateOffsets:
    def test_in_range_ok(self, profile_3090):
        co.validate_offsets(profile_3090, gpu=100, mem=500)
        co.validate_offsets(profile_3090, gpu=0, mem=0)

    def test_gpu_too_high_raises(self, profile_3090):
        with pytest.raises(ValueError, match="gpu"):
            co.validate_offsets(profile_3090, gpu=999, mem=500)

    def test_mem_too_high_raises(self, profile_3090):
        with pytest.raises(ValueError, match="mem"):
            co.validate_offsets(profile_3090, gpu=100, mem=9999)

    def test_negative_offsets_allowed(self, profile_3090):
        # Underclocking légitime
        co.validate_offsets(profile_3090, gpu=-50, mem=-100)


# ─────────────────────────── parse_offsets_query ───────────────────────────


class TestParseOffsetsQuery:
    def test_parses_standard_output(self):
        out = """
  Attribute 'GPUGraphicsClockOffsetAllPerformanceLevels' (desktop:0[gpu:0]): 100.
  Attribute 'GPUMemoryTransferRateOffsetAllPerformanceLevels' (desktop:0[gpu:0]): 500.
"""
        r = co.parse_offsets_query(out)
        assert r["gpu"] == 100
        assert r["mem"] == 500

    def test_handles_negative_values(self):
        out = """
  Attribute 'GPUGraphicsClockOffsetAllPerformanceLevels' (desktop:0[gpu:0]): -50.
  Attribute 'GPUMemoryTransferRateOffsetAllPerformanceLevels' (desktop:0[gpu:0]): -100.
"""
        r = co.parse_offsets_query(out)
        assert r["gpu"] == -50
        assert r["mem"] == -100

    def test_missing_attrs_returns_none(self):
        r = co.parse_offsets_query("garbage")
        assert r["gpu"] is None
        assert r["mem"] is None


# ──────────────────────────────── can_enable ───────────────────────────────


class TestCanEnable:
    def test_coolbits_enabled_ok(self):
        ok, reason = co.can_enable(coolbits_info={"enabled": True, "value": 12})
        assert ok is True

    def test_coolbits_absent(self):
        ok, reason = co.can_enable(coolbits_info={"enabled": False, "value": None})
        assert ok is False
        assert "coolbits" in reason.lower()

    def test_coolbits_value_too_low(self):
        # Bit 3 (value=8) = sliders OC. Sans ce bit, pas de contrôle.
        ok, reason = co.can_enable(coolbits_info={"enabled": True, "value": 4})  # juste fan
        assert ok is False
        assert "coolbits" in reason.lower()


# ───────────────────────────── get_current_offsets ─────────────────────────


class TestGetCurrentOffsets:
    def test_calls_nvidia_settings_with_display_xauth(self, monkeypatch):
        captured = {}

        def fake_run(cmd, *args, env=None, **kwargs):
            captured["cmd"] = cmd
            captured["env"] = env
            return _FakeCompleted(
                stdout="  Attribute 'GPUGraphicsClockOffsetAllPerformanceLevels' "
                       "(desktop:0[gpu:0]): 100.\n"
                       "  Attribute 'GPUMemoryTransferRateOffsetAllPerformanceLevels' "
                       "(desktop:0[gpu:0]): 500.\n",
                returncode=0,
            )

        monkeypatch.setattr(subprocess, "run", fake_run)
        r = co.get_current_offsets(display=":0", xauthority="/home/x/.Xauthority")
        assert r["gpu"] == 100
        assert r["mem"] == 500
        # nvidia-settings est appelé
        assert captured["cmd"][0] == "nvidia-settings"
        # DISPLAY et XAUTHORITY passés via env
        assert captured["env"]["DISPLAY"] == ":0"
        assert captured["env"]["XAUTHORITY"] == "/home/x/.Xauthority"

    def test_returns_none_on_failure(self, monkeypatch):
        def fake_run(cmd, *args, **kwargs):
            return _FakeCompleted(returncode=1, stderr="Bad handle")
        monkeypatch.setattr(subprocess, "run", fake_run)
        r = co.get_current_offsets(display=":0")
        assert r["gpu"] is None
        assert r["mem"] is None


# ──────────────────────────────── apply_offsets ────────────────────────────


class TestApplyOffsets:
    def test_validates_first(self, profile_3090, monkeypatch):
        def fake_run(cmd, *args, **kwargs):
            pytest.fail("ne devrait pas être appelé si validation échoue")
        monkeypatch.setattr(subprocess, "run", fake_run)
        with pytest.raises(ValueError):
            co.apply_offsets(profile_3090, gpu=9999, mem=500, display=":0")

    def test_calls_nvidia_settings_a(self, profile_3090, monkeypatch):
        called = []

        def fake_run(cmd, *args, env=None, **kwargs):
            called.append(cmd)
            return _FakeCompleted(
                stdout="Attribute 'GPUGraphicsClockOffsetAllPerformanceLevels' "
                       "(desktop:0[gpu:0]) assigned value 100.\n"
                       "Attribute 'GPUMemoryTransferRateOffsetAllPerformanceLevels' "
                       "(desktop:0[gpu:0]) assigned value 500.\n",
                returncode=0,
            )

        monkeypatch.setattr(subprocess, "run", fake_run)
        r = co.apply_offsets(profile_3090, gpu=100, mem=500, display=":0", xauthority="/x")
        assert r["ok"] is True
        # nvidia-settings doit avoir reçu les deux -a
        cmd = called[0]
        cmd_str = " ".join(cmd)
        assert "GPUGraphicsClockOffsetAllPerformanceLevels=100" in cmd_str
        assert "GPUMemoryTransferRateOffsetAllPerformanceLevels=500" in cmd_str

    def test_returns_error_on_subprocess_failure(self, profile_3090, monkeypatch):
        def fake_run(cmd, *args, **kwargs):
            return _FakeCompleted(stdout="", stderr="ERROR: Bad handle", returncode=1)
        monkeypatch.setattr(subprocess, "run", fake_run)
        r = co.apply_offsets(profile_3090, gpu=100, mem=500, display=":0")
        assert r["ok"] is False
        assert "error" in r.get("error", "").lower() or "ERROR" in r.get("error", "")
