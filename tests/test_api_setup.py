"""Tests pour les endpoints du wizard /api/setup/*."""
from __future__ import annotations

import os
import subprocess

import pytest

from gpu_dashboard import api
from gpu_dashboard.config import Config


@pytest.fixture
def ctx(tmp_path, monkeypatch):
    """Contexte minimaliste : config par défaut + profiles_dir factice."""
    profiles_dir = str(tmp_path / "profiles")
    os.makedirs(profiles_dir)
    return {
        "config": Config(defaults={"POWER_LIMIT_WRAPPER": "/nonexistent/wrapper"}),
        "profiles_dir": profiles_dir,
        "setup_required": True,
    }


# ────────────────────────── handle_setup_detect ────────────────────────────


class TestSetupDetect:
    def test_returns_env_and_modules(self, ctx):
        code, body = api.handle_setup_detect(ctx)
        assert code == 200
        assert body["ok"] is True
        assert "env" in body
        assert "modules" in body
        assert isinstance(body["modules"], list)
        # Au moins les 4 modules connus
        names = {m["name"] for m in body["modules"]}
        assert {"power_limit", "clock_offsets", "telegram_alerts", "oculink_watchdog"}.issubset(names)

    def test_setup_required_passed_through(self, ctx):
        code, body = api.handle_setup_detect(ctx)
        assert body["setup_required"] is True

    def test_env_includes_os_nvidia_coolbits(self, ctx):
        code, body = api.handle_setup_detect(ctx)
        env = body["env"]
        assert "os" in env
        assert "nvidia" in env
        assert "coolbits" in env


# ────────────────────────── handle_setup_recheck ───────────────────────────


class TestSetupRecheckPowerLimit:
    def test_wrapper_missing_returns_ok_false(self, ctx):
        code, body = api.handle_setup_recheck(ctx, "power_limit")
        assert code == 200
        assert body["ok"] is False
        assert "wrapper" in body["reason"].lower() or "absent" in body["reason"].lower()


class TestSetupRecheckClockOffsets:
    def test_handles_coolbits_detection(self, ctx):
        code, body = api.handle_setup_recheck(ctx, "clock_offsets")
        assert code == 200
        # Sur la machine de test on ne sait pas si Coolbits est configuré
        # mais la réponse doit toujours être structurée
        assert "ok" in body
        assert "reason" in body


class TestSetupRecheckTelegram:
    def test_no_token_returns_ok_false(self, ctx):
        code, body = api.handle_setup_recheck(ctx, "telegram_alerts")
        assert code == 200
        assert body["ok"] is False
        assert "token" in body["reason"].lower() or "missing" in body["reason"].lower()

    def test_with_token_returns_ok_true(self):
        cfg = Config(defaults={"TG_TOKEN": "abc:def", "TG_CHAT": "123456"})
        ctx = {"config": cfg}
        code, body = api.handle_setup_recheck(ctx, "telegram_alerts")
        assert code == 200
        assert body["ok"] is True


class TestSetupRecheckUnknownModule:
    def test_returns_400(self, ctx):
        code, body = api.handle_setup_recheck(ctx, "nonexistent_module")
        assert code == 400
        assert body["ok"] is False


# ────────────────────────── handle_setup_save ──────────────────────────────


class TestSetupSave:
    def test_writes_config_env(self, tmp_path, monkeypatch):
        # Redirige HOME vers tmp_path pour ne pas écraser le vrai config
        monkeypatch.setenv("HOME", str(tmp_path))
        ctx = {"config": Config(defaults={})}
        payload = {
            "modules": {"power_limit": True, "clock_offsets": False, "telegram_alerts": True},
            "port": 9000,
        }
        code, body = api.handle_setup_save(ctx, payload)
        assert code == 200
        assert body["ok"] is True
        # Le fichier doit exister
        assert os.path.isfile(body["path"])
        with open(body["path"]) as f:
            content = f.read()
        assert "DASHBOARD_PORT=9000" in content
        assert "MODULE_POWER_LIMIT=1" in content
        assert "MODULE_CLOCK_OFFSETS=0" in content
        assert "MODULE_TELEGRAM_ALERTS=1" in content

    def test_invalid_modules_returns_400(self, tmp_path, monkeypatch):
        monkeypatch.setenv("HOME", str(tmp_path))
        ctx = {"config": Config(defaults={})}
        code, body = api.handle_setup_save(ctx, {"modules": "not-a-dict"})
        assert code == 400

    def test_invalid_port_returns_400(self, tmp_path, monkeypatch):
        monkeypatch.setenv("HOME", str(tmp_path))
        ctx = {"config": Config(defaults={})}
        code, body = api.handle_setup_save(ctx, {"modules": {}, "port": "abc"})
        assert code == 400

    def test_creates_parent_dir(self, tmp_path, monkeypatch):
        monkeypatch.setenv("HOME", str(tmp_path))
        ctx = {"config": Config(defaults={})}
        code, body = api.handle_setup_save(ctx, {"modules": {}})
        # ~/.config/gpu-dashboard/ doit avoir été créé
        assert os.path.isdir(os.path.dirname(body["path"]))
