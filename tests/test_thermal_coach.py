"""R&D #5.1 — Thermal headroom coach tests."""
import time
from gpu_dashboard import api
from gpu_dashboard.config import Config


class FakeSampler:
    """Minimal stub for ctx['sampler']."""
    def __init__(self, samples):
        self._samples = samples
    def snapshot(self):
        return list(self._samples)


def _ctx(samples=None):
    return {"config": Config(defaults={}), "sampler": FakeSampler(samples or [])}


def test_linear_fit_basic():
    slope, intercept = api._linear_fit([0, 1, 2, 3], [1, 2, 3, 4])
    assert abs(slope - 1.0) < 1e-6
    assert abs(intercept - 1.0) < 1e-6


def test_linear_fit_constant_returns_zero_slope():
    slope, _ = api._linear_fit([1, 1, 1], [5, 6, 7])
    assert slope == 0


def test_no_sampler_returns_unavailable():
    code, body = api.handle_thermal_coach({"config": Config(defaults={}), "sampler": None})
    assert code == 200
    assert body["available"] is False
    assert "no sampler" in body["reason"].lower()


def test_too_few_samples_returns_unavailable():
    samples = [{"ts": 1, "temp": 50}, {"ts": 2, "temp": 51}]
    code, body = api.handle_thermal_coach(_ctx(samples))
    assert code == 200
    assert body["available"] is False


def test_stable_idle_temp_returns_high_headroom():
    """Temp flat at 40°C → headroom ~43°C, no throttle projected, suggest fan gentler."""
    now = int(time.time())
    samples = [{"ts": now + i * 5, "temp": 40.0} for i in range(20)]
    code, body = api.handle_thermal_coach(_ctx(samples))
    assert code == 200
    assert body["available"] is True
    assert body["headroom_c"] == 43.0  # 83 - 40
    assert body["projected_throttle_s"] is None  # flat → no projection
    assert body["suggested_fan_delta_pct"] == -10
    assert body["suggested_msg_key"] == "fan_can_be_gentler"


def test_warming_trend_projects_throttle_time():
    """Temp rising 1°C / minute → at 65°C should project throttle in ~18 minutes."""
    now = int(time.time())
    # 20 samples spaced 5s apart, temp rising 1°C every 60s → 1/12 °C per sample
    samples = [{"ts": now + i * 5, "temp": 65 + (i * 5 / 60)} for i in range(60)]
    code, body = api.handle_thermal_coach(_ctx(samples))
    assert code == 200
    assert body["available"] is True
    # Last sample temp ~ 65 + 59*5/60 = 69.9°C, slope ~ 1°C/min
    assert body["headroom_c"] < 20
    assert body["projected_throttle_s"] is not None
    # 83 - 69.9 ≈ 13°C at 1°C/min ≈ 13min ≈ 780s ; tolerate ±60s
    assert 700 < body["projected_throttle_s"] < 900


def test_imminent_throttle_warning():
    """Near slowdown temp → urgent fan help."""
    now = int(time.time())
    # Temp at 80°C, rising 0.1°C per 5s ≈ 1.2°C/min → throttle in ~150s
    samples = [{"ts": now + i * 5, "temp": 80 + i * 0.1} for i in range(20)]
    code, body = api.handle_thermal_coach(_ctx(samples))
    assert code == 200
    assert body["headroom_c"] < 5
    assert body["suggested_msg_key"] == "fan_needs_help"


def test_critical_headroom_under_5_returns_warning():
    """Headroom <5°C even if flat → fan help recommended."""
    now = int(time.time())
    samples = [{"ts": now + i * 5, "temp": 79.0} for i in range(20)]
    code, body = api.handle_thermal_coach(_ctx(samples))
    assert code == 200
    assert body["headroom_c"] == 4.0
    assert body["suggested_msg_key"] == "fan_needs_help"
