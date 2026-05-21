"""Tests pour gpu_dashboard.modules.power_limit.

Le module gère :
- Validation de la valeur W contre les limites du profil
- Parsing de la sortie `nvidia-smi --query-gpu=power.limit`
- Vérification d'éligibilité (wrapper présent + sudoers passwordless OK)
- Application du power-limit via `sudo -n /usr/local/bin/set-power-limit <W>`
- Lecture du power-limit actuel
"""
from __future__ import annotations

import os
import subprocess
import pytest

from gpu_dashboard.modules import power_limit as pl


# ───────────────────────────── helpers / fixtures ──────────────────────────


@pytest.fixture
def profile_3090():
    return {
        "power": {"min": 100, "max": 350, "stock": 350, "sweet_spot": 250},
    }


class _FakeCompleted:
    def __init__(self, stdout="", stderr="", returncode=0):
        self.stdout = stdout
        self.stderr = stderr
        self.returncode = returncode


# ────────────────────────────── validate_watts ─────────────────────────────


class TestValidateWatts:
    def test_in_range_ok(self, profile_3090):
        # ne raise pas
        pl.validate_watts(profile_3090, 250)
        pl.validate_watts(profile_3090, 100)  # min
        pl.validate_watts(profile_3090, 350)  # max

    def test_below_min_raises(self, profile_3090):
        with pytest.raises(ValueError, match="out of range"):
            pl.validate_watts(profile_3090, 50)

    def test_above_max_raises(self, profile_3090):
        with pytest.raises(ValueError, match="out of range"):
            pl.validate_watts(profile_3090, 500)

    def test_non_integer_raises(self, profile_3090):
        with pytest.raises((ValueError, TypeError)):
            pl.validate_watts(profile_3090, "abc")

    def test_default_range_when_profile_missing_fields(self):
        # profil dégénéré → fallback 100-350
        pl.validate_watts({}, 250)
        with pytest.raises(ValueError):
            pl.validate_watts({}, 999)


# ─────────────────────── parse_nvidia_smi_power_limit ──────────────────────


class TestParseNvidiaSmiPowerLimit:
    def test_standard_output(self):
        assert pl.parse_nvidia_smi_power_limit("250.00 W\n") == 250

    def test_integer_value(self):
        assert pl.parse_nvidia_smi_power_limit("350 W\n") == 350

    def test_with_extra_whitespace(self):
        assert pl.parse_nvidia_smi_power_limit("  280.50 W  \n") == 281

    def test_invalid_returns_none(self):
        assert pl.parse_nvidia_smi_power_limit("garbage") is None

    def test_empty_returns_none(self):
        assert pl.parse_nvidia_smi_power_limit("") is None


# ──────────────────────────────── can_enable ───────────────────────────────


class TestCanEnable:
    def test_wrapper_missing(self, tmp_path):
        ok, reason = pl.can_enable(wrapper_path=str(tmp_path / "nope"))
        assert ok is False
        assert "wrapper" in reason.lower() or "absent" in reason.lower()

    def test_wrapper_not_executable(self, tmp_path):
        f = tmp_path / "wrapper"
        f.write_text("#!/bin/sh\nexit 0\n")
        # Pas de chmod +x
        ok, reason = pl.can_enable(wrapper_path=str(f))
        assert ok is False
        assert "exécutable" in reason.lower() or "executable" in reason.lower()

    def test_sudo_passwordless_ok(self, tmp_path, monkeypatch):
        f = tmp_path / "wrapper"
        f.write_text("#!/bin/sh\nexit 0\n")
        f.chmod(0o755)

        def fake_run(cmd, *args, **kwargs):
            # `sudo -n -l <wrapper>` doit réussir si sudoers OK
            if cmd[:3] == ["sudo", "-n", "-l"]:
                return _FakeCompleted(returncode=0)
            return _FakeCompleted(returncode=127)

        monkeypatch.setattr(subprocess, "run", fake_run)
        ok, reason = pl.can_enable(wrapper_path=str(f))
        assert ok is True

    def test_sudo_password_required(self, tmp_path, monkeypatch):
        f = tmp_path / "wrapper"
        f.write_text("#!/bin/sh\nexit 0\n")
        f.chmod(0o755)

        def fake_run(cmd, *args, **kwargs):
            if cmd[:3] == ["sudo", "-n", "-l"]:
                return _FakeCompleted(returncode=1, stderr="password required")
            return _FakeCompleted(returncode=127)

        monkeypatch.setattr(subprocess, "run", fake_run)
        ok, reason = pl.can_enable(wrapper_path=str(f))
        assert ok is False
        assert "sudoers" in reason.lower() or "password" in reason.lower()


# ─────────────────────────────── apply_power_limit ─────────────────────────


class TestApplyPowerLimit:
    def test_validates_before_calling(self, profile_3090, monkeypatch):
        def fake_run(cmd, *args, **kwargs):
            pytest.fail("subprocess.run ne devrait PAS être appelé si validation échoue")
        monkeypatch.setattr(subprocess, "run", fake_run)
        with pytest.raises(ValueError):
            pl.apply_power_limit(profile_3090, 999, wrapper_path="/usr/local/bin/set-power-limit")

    def test_calls_wrapper_via_sudo(self, profile_3090, monkeypatch):
        calls = []

        def fake_run(cmd, *args, **kwargs):
            calls.append(cmd)
            return _FakeCompleted(
                stdout="Power limit for GPU 0000:01:00.0 was set to 250.00 W from 280.00 W.\n",
                returncode=0,
            )

        monkeypatch.setattr(subprocess, "run", fake_run)
        result = pl.apply_power_limit(profile_3090, 250, wrapper_path="/usr/local/bin/set-power-limit")
        assert result["ok"] is True
        assert result["watts"] == 250
        # Le wrapper doit avoir été appelé via sudo -n
        assert calls[0] == ["sudo", "-n", "/usr/local/bin/set-power-limit", "250"]

    def test_returns_error_on_wrapper_failure(self, profile_3090, monkeypatch):
        def fake_run(cmd, *args, **kwargs):
            return _FakeCompleted(stdout="", stderr="ERROR: out of range", returncode=2)

        monkeypatch.setattr(subprocess, "run", fake_run)
        result = pl.apply_power_limit(profile_3090, 250, wrapper_path="/usr/local/bin/set-power-limit")
        assert result["ok"] is False
        assert "error" in result.get("error", "").lower() or "ERROR" in result.get("error", "")


# ──────────────────────────── get_current_limit ────────────────────────────


class TestGetCurrentLimit:
    def test_reads_via_nvidia_smi(self, monkeypatch):
        def fake_run(cmd, *args, **kwargs):
            assert cmd[0] == "nvidia-smi"
            assert "--query-gpu=power.limit" in cmd
            return _FakeCompleted(stdout="250.00 W\n", returncode=0)

        monkeypatch.setattr(subprocess, "run", fake_run)
        assert pl.get_current_limit() == 250

    def test_returns_none_if_nvidia_smi_fails(self, monkeypatch):
        def fake_run(cmd, *args, **kwargs):
            return _FakeCompleted(returncode=1, stderr="No devices found")
        monkeypatch.setattr(subprocess, "run", fake_run)
        assert pl.get_current_limit() is None

    def test_returns_none_if_nvidia_smi_missing(self, monkeypatch):
        def fake_run(cmd, *args, **kwargs):
            raise FileNotFoundError("not installed")
        monkeypatch.setattr(subprocess, "run", fake_run)
        assert pl.get_current_limit() is None
