"""Tests pour gpu_dashboard.install — phase de recommandation + générateurs.

Les fonctions interactives (input(), main()) ne sont pas testées ici, mais
les fonctions pures (recommend_modules, generate_*) le sont rigoureusement.
"""
from __future__ import annotations

import pytest

from gpu_dashboard.install import (
    recommend_modules,
    generate_config_env,
    generate_sudoers,
    generate_systemd_unit,
    generate_coolbits_xorg_conf,
    generate_power_limit_wrapper,
)


# ─────────────────────────── recommend_modules ─────────────────────────────


def _env(coolbits_value=None, external=False, vm=False, wrapper_exists=False):
    """Helper : construit un env de test avec defaults sains."""
    return {
        "nvidia": {"available": True, "gpus": [{"name": "RTX 3090", "bus_id": "0000:01:00.0"}]},
        "coolbits": {"enabled": coolbits_value is not None, "value": coolbits_value},
        "external_gpu": {"link_width": 4 if external else 16, "likely_external": external},
        "virt": {"is_vm": vm, "type": "kvm" if vm else "none"},
        "power_wrapper_exists": wrapper_exists,
    }


class TestRecommendModules:
    def test_always_returns_known_modules(self):
        recs = recommend_modules(_env())
        names = {r["name"] for r in recs}
        assert "power_limit" in names
        assert "clock_offsets" in names
        assert "telegram_alerts" in names
        assert "oculink_watchdog" in names

    def test_power_limit_unavailable_without_wrapper(self):
        recs = recommend_modules(_env(wrapper_exists=False))
        pl = next(r for r in recs if r["name"] == "power_limit")
        assert pl["available"] is False
        assert "wrapper" in pl["reason"].lower()

    def test_power_limit_available_with_wrapper(self):
        recs = recommend_modules(_env(wrapper_exists=True))
        pl = next(r for r in recs if r["name"] == "power_limit")
        assert pl["available"] is True

    def test_clock_offsets_needs_coolbits_oc_bit(self):
        # Coolbits=4 = fan seulement (bit 2), pas le bit OC (bit 3 = 8)
        recs = recommend_modules(_env(coolbits_value=4))
        co = next(r for r in recs if r["name"] == "clock_offsets")
        assert co["available"] is False

        # Coolbits=12 = fan(4) + OC(8) → OK
        recs = recommend_modules(_env(coolbits_value=12))
        co = next(r for r in recs if r["name"] == "clock_offsets")
        assert co["available"] is True

    def test_oculink_recommended_when_external_link(self):
        recs = recommend_modules(_env(external=True))
        ol = next(r for r in recs if r["name"] == "oculink_watchdog")
        assert ol["recommend"] is True

    def test_oculink_recommended_when_vm(self):
        recs = recommend_modules(_env(vm=True))
        ol = next(r for r in recs if r["name"] == "oculink_watchdog")
        assert ol["recommend"] is True

    def test_oculink_not_recommended_on_internal_bare_metal(self):
        recs = recommend_modules(_env(external=False, vm=False))
        ol = next(r for r in recs if r["name"] == "oculink_watchdog")
        assert ol["recommend"] is False

    def test_telegram_always_available_but_not_recommended_by_default(self):
        recs = recommend_modules(_env())
        tg = next(r for r in recs if r["name"] == "telegram_alerts")
        assert tg["available"] is True
        # Pas activé par défaut : ça demande un token, opt-in user
        assert tg["recommend"] is False


# ─────────────────────── generate_config_env ───────────────────────────────


class TestGenerateConfigEnv:
    def test_includes_all_modules_flags(self):
        choices = {
            "power_limit": True,
            "clock_offsets": False,
            "telegram_alerts": True,
            "oculink_watchdog": False,
            "fan_curve": False,
        }
        s = generate_config_env(choices, port=9999, power_default=250)
        assert "MODULE_POWER_LIMIT=1" in s
        assert "MODULE_CLOCK_OFFSETS=0" in s
        assert "MODULE_TELEGRAM_ALERTS=1" in s
        assert "MODULE_OCULINK_WATCHDOG=0" in s

    def test_includes_dashboard_port(self):
        s = generate_config_env({}, port=8080, power_default=250)
        assert "DASHBOARD_PORT=8080" in s

    def test_includes_power_default(self):
        s = generate_config_env({}, port=9999, power_default=280)
        assert "POWER_LIMIT_DEFAULT=280" in s

    def test_has_header_comment(self):
        s = generate_config_env({}, port=9999, power_default=250)
        assert s.lstrip().startswith("#")


# ─────────────────────────── generate_sudoers ──────────────────────────────


class TestGenerateSudoers:
    def test_includes_user_and_wrapper(self):
        s = generate_sudoers(user="alice", wrapper_path="/usr/local/bin/set-power-limit")
        assert "alice" in s
        assert "/usr/local/bin/set-power-limit" in s
        assert "NOPASSWD" in s

    def test_no_wildcards(self):
        """Pour la sécurité : pas de wildcard dans la ligne sudoers."""
        s = generate_sudoers(user="alice", wrapper_path="/usr/local/bin/set-power-limit")
        # On veut spécifiquement le wrapper, pas un pattern large
        assert "*" not in s.replace("# *", "")  # autorisé dans les commentaires


# ───────────────────────── generate_systemd_unit ───────────────────────────


class TestGeneratePowerLimitWrapper:
    def test_has_shebang(self):
        s = generate_power_limit_wrapper()
        assert s.startswith("#!/usr/bin/env bash")

    def test_validates_range(self):
        s = generate_power_limit_wrapper()
        assert "W < 100" in s
        assert "W > 350" in s

    def test_calls_nvidia_smi(self):
        s = generate_power_limit_wrapper()
        assert "/usr/bin/nvidia-smi -pl" in s

    def test_persists_to_default(self):
        s = generate_power_limit_wrapper()
        assert "/etc/default/gpu-powerlimit" in s


class TestGenerateCoolbitsXorgConf:
    def test_simple_drop_in_has_coolbits_and_bus_id(self):
        s = generate_coolbits_xorg_conf(bus_id="PCI:1:0:0", headless=False)
        assert 'Option "Coolbits" "12"' in s
        assert 'BusID "PCI:1:0:0"' in s
        # Pas de ServerLayout ni AllowEmpty pour le cas simple
        assert "AllowEmptyInitialConfiguration" not in s
        assert "ServerLayout" not in s

    def test_headless_includes_allow_empty(self):
        s = generate_coolbits_xorg_conf(bus_id="PCI:1:0:0", headless=True)
        assert 'Option "Coolbits" "12"' in s
        assert 'BusID "PCI:1:0:0"' in s
        assert 'Option "AllowEmptyInitialConfiguration" "true"' in s
        assert 'Option "ConnectedMonitor" "DFP-0"' in s
        # Sections supplémentaires nécessaires
        assert "ServerLayout" in s
        assert "Monitor" in s
        assert "Screen" in s

    def test_headless_disables_auto_add_gpu(self):
        s = generate_coolbits_xorg_conf(headless=True)
        assert 'Option "AutoAddGPU" "false"' in s
        assert 'Option "AutoBindGPU" "false"' in s

    def test_default_bus_id(self):
        # Default est PCI:1:0:0 (bus 01:00.0, cas le plus commun)
        s = generate_coolbits_xorg_conf()
        assert 'BusID "PCI:1:0:0"' in s


class TestGenerateSystemdUnit:
    def test_user_service_with_exec_start(self):
        s = generate_systemd_unit(
            description="GPU dashboard",
            exec_start="/usr/bin/python3 -m gpu_dashboard.server",
            env_file="/home/alice/.config/gpu-dashboard/config.env",
        )
        assert "[Unit]" in s
        assert "[Service]" in s
        assert "[Install]" in s
        assert "Description=GPU dashboard" in s
        assert "ExecStart=/usr/bin/python3 -m gpu_dashboard.server" in s
        assert "EnvironmentFile=-/home/alice/.config/gpu-dashboard/config.env" in s

    def test_wantedby_target_default(self):
        s = generate_systemd_unit(
            description="x", exec_start="/bin/true", env_file="/tmp/x"
        )
        assert "WantedBy=default.target" in s

    def test_restart_on_failure(self):
        s = generate_systemd_unit(
            description="x", exec_start="/bin/true", env_file="/tmp/x"
        )
        assert "Restart=" in s
