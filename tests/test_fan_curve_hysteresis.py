"""R&D #4.4 — fan curve hysteresis (ramp-down delay + temp delta)."""
from gpu_dashboard.modules.fan_curve import FanCurveDaemon


def _d(hysteresis_s=15.0, hysteresis_c=3.0):
    d = FanCurveDaemon(
        curve=[[30, 0], [60, 50], [80, 100]],
        hysteresis_s=hysteresis_s, hysteresis_c=hysteresis_c,
    )
    return d


def test_first_call_always_applies():
    d = _d()
    assert d._should_apply(target_pct=50, now_temp=60.0, now_ts=100.0) is True


def test_same_pct_skips():
    d = _d()
    d._last_pct = 50
    d._last_change_ts = 100.0
    d._last_change_temp = 60.0
    assert d._should_apply(target_pct=50, now_temp=60.0, now_ts=105.0) is False


def test_ramp_up_always_applies_immediately():
    d = _d()
    d._last_pct = 30
    d._last_change_ts = 100.0
    d._last_change_temp = 50.0
    # Temp jumped up → fan must speed up NOW even if hysteresis_s not elapsed
    assert d._should_apply(target_pct=70, now_temp=65.0, now_ts=101.0) is True


def test_ramp_down_within_time_window_blocked():
    d = _d(hysteresis_s=15.0, hysteresis_c=3.0)
    d._last_pct = 70
    d._last_change_ts = 100.0
    d._last_change_temp = 75.0
    # 5s elapsed (< 15s window), temp drop 1°C (< 3°C threshold) → blocked
    assert d._should_apply(target_pct=50, now_temp=74.0, now_ts=105.0) is False


def test_ramp_down_after_time_elapsed():
    d = _d(hysteresis_s=15.0, hysteresis_c=3.0)
    d._last_pct = 70
    d._last_change_ts = 100.0
    d._last_change_temp = 75.0
    # 20s elapsed (>= 15s) → allow ramp-down even if temp barely dropped
    assert d._should_apply(target_pct=50, now_temp=74.5, now_ts=120.0) is True


def test_ramp_down_with_big_temp_drop():
    d = _d(hysteresis_s=15.0, hysteresis_c=3.0)
    d._last_pct = 70
    d._last_change_ts = 100.0
    d._last_change_temp = 75.0
    # Only 2s elapsed, but temp dropped 5°C (>= 3°C) → allow ramp-down
    assert d._should_apply(target_pct=50, now_temp=70.0, now_ts=102.0) is True


def test_default_hysteresis_values():
    """Default hysteresis : 3°C drop tolerance, 15s ramp-down delay."""
    d = FanCurveDaemon(curve=[[30, 0], [80, 100]])
    assert d._hysteresis_c == 3.0
    assert d._hysteresis_s == 15.0
