"""Tests for /api/prom — Prometheus text-format exporter."""
from __future__ import annotations

import pytest

from gpu_dashboard import api


@pytest.fixture
def ctx_alive(monkeypatch):
    monkeypatch.setattr(api, "_gpu_card_snapshot", lambda gpu_index=0: {
        "alive": True, "index": 0, "name": "RTX 3090",
        "temp": 55, "fan_pct": 40,
        "power": 230.5, "power_limit": 280.0,
        "util_gpu": 75, "mem_used_mib": 12000, "mem_total_mib": 24576,
    })
    return {"config": {"get_int": lambda *a, **k: 0}}


def _cfg_with_index(idx=0):
    class C:
        def get_int(self, k, default=0): return idx if k == "GPU_INDEX" else default
        def get(self, k, default=""): return default
        def get_bool(self, k, default=False): return default
    return C()


class TestHandleProm:
    def test_returns_text_plain(self, monkeypatch):
        monkeypatch.setattr(api, "_gpu_card_snapshot", lambda gpu_index=0: {
            "alive": True, "name": "RTX 3090", "temp": 55, "fan_pct": 40,
            "power": 230.5, "power_limit": 280.0,
            "util_gpu": 75, "mem_used_mib": 12000, "mem_total_mib": 24576,
        })
        code, body = api.handle_prom({"config": _cfg_with_index()})
        assert code == 200
        assert isinstance(body, str)

    def test_includes_standard_metrics(self, monkeypatch):
        monkeypatch.setattr(api, "_gpu_card_snapshot", lambda gpu_index=0: {
            "alive": True, "name": "RTX 3090", "temp": 55, "fan_pct": 40,
            "power": 230.5, "power_limit": 280.0,
            "util_gpu": 75, "mem_used_mib": 12000, "mem_total_mib": 24576,
        })
        _, body = api.handle_prom({"config": _cfg_with_index()})
        # Each metric should appear as a line with TYPE + value
        for metric in ("gpu_temp_celsius", "gpu_power_watts", "gpu_fan_percent",
                       "gpu_util_percent", "gpu_memory_used_bytes", "gpu_alive"):
            assert metric in body, f"missing metric {metric} in:\n{body}"

    def test_dead_gpu_alive_is_zero(self, monkeypatch):
        monkeypatch.setattr(api, "_gpu_card_snapshot", lambda gpu_index=0: {
            "alive": False, "name": "?",
        })
        _, body = api.handle_prom({"config": _cfg_with_index()})
        assert "gpu_alive 0" in body or "gpu_alive{" in body

    def test_help_and_type_lines_present(self, monkeypatch):
        monkeypatch.setattr(api, "_gpu_card_snapshot", lambda gpu_index=0: {
            "alive": True, "name": "X", "temp": 50, "fan_pct": 40,
            "power": 200.0, "power_limit": 250.0,
            "util_gpu": 60, "mem_used_mib": 8000, "mem_total_mib": 24576,
        })
        _, body = api.handle_prom({"config": _cfg_with_index()})
        # Prometheus format requires # HELP and # TYPE lines
        assert "# HELP" in body
        assert "# TYPE" in body
