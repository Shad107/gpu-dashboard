"""Tests for the CLI --status output (pure rendering function)."""
from __future__ import annotations

import re

import pytest

from gpu_dashboard.cli_status import render_status_lines


_ANSI = re.compile(r"\x1b\[[0-9;]*m")


def _strip(s):
    return _ANSI.sub("", s)


class TestRenderStatusLines:
    def test_alive_gpu_shows_temp_and_power(self):
        state = {"gpu": {"alive": True, "name": "RTX 3090",
                         "temp": 55, "fan_pct": 40,
                         "power": 230.5, "power_limit": 280.0,
                         "util_gpu": 75,
                         "mem_used_mib": 12000, "mem_total_mib": 24576}}
        lines = render_status_lines(state)
        joined = "\n".join(_strip(l) for l in lines)
        assert "RTX 3090" in joined
        assert "55°C" in joined
        assert "230" in joined  # power
        assert "75%" in joined  # util

    def test_dead_gpu_shows_error(self):
        state = {"gpu": {"alive": False, "name": "?"}}
        lines = render_status_lines(state)
        joined = "\n".join(_strip(l) for l in lines)
        assert "not alive" in joined.lower() or "unreachable" in joined.lower()

    def test_electricity_block(self):
        state = {"gpu": {"alive": True, "name": "X", "temp": 50, "fan_pct": 40,
                         "power": 200, "power_limit": 250, "util_gpu": 0,
                         "mem_used_mib": 0, "mem_total_mib": 24576}}
        elec = {"ok": True, "avg_power_watts": 250, "daily_cost": 1.50,
                "monthly_cost": 45.0, "currency": "EUR"}
        lines = render_status_lines(state, electricity=elec)
        joined = "\n".join(_strip(l) for l in lines)
        assert "45.00" in joined  # monthly cost
        assert "1.50" in joined   # daily cost
        assert "€" in joined or "EUR" in joined

    def test_llm_throughput_block(self):
        state = {"gpu": {"alive": True, "name": "X", "temp": 50, "fan_pct": 40,
                         "power": 200, "power_limit": 250, "util_gpu": 50,
                         "mem_used_mib": 8000, "mem_total_mib": 24576}}
        llm = {"available": True, "tokens_generated_total": 12345, "tokens_per_watt": 0.85}
        lines = render_status_lines(state, llm=llm)
        joined = "\n".join(_strip(l) for l in lines)
        assert "12,345" in joined or "12345" in joined
        assert "0.85" in joined
        assert "tok/W" in joined

    def test_watchdog_block(self):
        state = {"gpu": {"alive": True, "name": "X", "temp": 50, "fan_pct": 40,
                         "power": 200, "power_limit": 250, "util_gpu": 0,
                         "mem_used_mib": 0, "mem_total_mib": 24576},
                 "watchdog": {"available": True, "drops": 2, "last_uptime": "6h21m"}}
        lines = render_status_lines(state)
        joined = "\n".join(_strip(l) for l in lines)
        assert "OcuLink" in joined
        assert "6h21m" in joined
        assert "2 drops" in joined

    def test_health_block(self):
        state = {"gpu": {"alive": True, "name": "X", "temp": 50, "fan_pct": 40,
                         "power": 200, "power_limit": 250, "util_gpu": 0,
                         "mem_used_mib": 0, "mem_total_mib": 24576}}
        health = {"status": "ok", "components": {"gpu": True, "sampler": True, "storage": True}}
        lines = render_status_lines(state, health=health)
        joined = "\n".join(_strip(l) for l in lines)
        assert "Health" in joined
        assert "ok" in joined
        assert "✓" in joined or "gpu" in joined
