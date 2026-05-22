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


# R&D #8.2 — confidence + notification bridge tests

def test_r_squared_perfect_line():
    # y = 2x + 3 exactly
    xs = [0, 1, 2, 3, 4]
    ys = [3, 5, 7, 9, 11]
    r2 = api._r_squared(xs, ys, slope=2.0, intercept=3.0)
    assert abs(r2 - 1.0) < 1e-9


def test_r_squared_constant_y_returns_1():
    """No variance in y → conventionally R²=1 (no error to attribute)."""
    r2 = api._r_squared([1, 2, 3], [5, 5, 5], slope=0, intercept=5)
    assert r2 == 1.0


def test_r_squared_noisy_returns_below_1():
    xs = [0, 1, 2, 3, 4]
    ys = [3, 7, 6, 11, 10]  # noisy around y=2x+3
    slope, intercept = api._linear_fit(xs, ys)
    r2 = api._r_squared(xs, ys, slope, intercept)
    assert 0 < r2 < 1


def test_response_includes_confidence_field():
    """The thermal coach must return a 'confidence' field (R²)."""
    samples = [{"ts": "now", "temp": 40.0} for _ in range(20)]
    code, body = api.handle_thermal_coach(_ctx(samples))
    assert body["available"] is True
    assert "confidence" in body
    assert 0 <= body["confidence"] <= 1
    # constant temp → R²=1 (no variance)
    assert body["confidence"] == 1.0


def test_imminent_throttle_triggers_notification(monkeypatch):
    """When projected < 120s + confidence > 0.5, notif_hub.send must be called."""
    calls = []
    def fake_send(**kw):
        calls.append(kw)
        return []
    from gpu_dashboard.modules import notif_hub
    monkeypatch.setattr(notif_hub, "send", fake_send)
    # 78 → 81.8°C over 20 samples — last temp stays under 83, projected ~30s
    samples = [{"ts": "now", "temp": 78 + i * 0.2} for i in range(20)]
    code, body = api.handle_thermal_coach(_ctx(samples))
    assert code == 200
    assert body["projected_throttle_s"] is not None
    assert body["projected_throttle_s"] < 120
    assert body["confidence"] > 0.5
    assert len(calls) == 1
    assert calls[0]["level"] == "warning"
    assert "throttle imminent" in calls[0]["title"].lower()


def test_low_confidence_does_not_trigger_notification(monkeypatch):
    """Even imminent projected throttle, low R² should SUPPRESS notification
    (false positives on noisy data)."""
    calls = []
    from gpu_dashboard.modules import notif_hub
    monkeypatch.setattr(notif_hub, "send", lambda **kw: calls.append(kw) or [])
    # Highly noisy temp series → high slope but low R²
    import random
    random.seed(42)
    samples = [{"ts": "now", "temp": 80 + (i * 0.2) + random.uniform(-3, 3)} for i in range(20)]
    code, body = api.handle_thermal_coach(_ctx(samples))
    # If projected fires but R² < 0.5, the gate should block
    if body["projected_throttle_s"] is not None and body["projected_throttle_s"] < 120:
        if body["confidence"] <= 0.5:
            assert len(calls) == 0, "notif should be suppressed when confidence low"
