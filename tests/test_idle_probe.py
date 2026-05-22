"""R&D #17.7 — idle-rig probe tests."""
import time
import pytest
from unittest.mock import MagicMock, patch
from gpu_dashboard.api import idle_probe as ip
from gpu_dashboard import api


def _snap(temp=50, util=20, power=80, alive=True, name="RTX 3090"):
    return {
        "alive": alive, "name": name, "temp": temp,
        "util_gpu": util, "power": power, "power_limit": 350,
        "mem_used_mib": 8192, "mem_total_mib": 24576,
    }


# ── _classify ────────────────────────────────────────────────────────────


def test_classify_idle_low_util_low_power():
    assert ip._classify(_snap(util=5, power=15), 15, 30) is True


def test_classify_active_high_util():
    assert ip._classify(_snap(util=80, power=15), 15, 30) is False


def test_classify_active_high_power_low_util():
    """High wattage despite low util → active (compile shader, etc.)."""
    assert ip._classify(_snap(util=5, power=200), 15, 30) is False


def test_classify_offline_treated_as_idle():
    assert ip._classify(_snap(alive=False), 15, 30) is True


def test_classify_handles_empty_snap():
    assert ip._classify({}, 15, 30) is True


# ── _format_duration ─────────────────────────────────────────────────────


def test_format_duration_seconds():
    assert ip._format_duration(30) == "30s"


def test_format_duration_minutes():
    assert ip._format_duration(180) == "3m"


def test_format_duration_hours():
    assert ip._format_duration(7200) == "2h"


def test_format_duration_days():
    assert ip._format_duration(172800) == "2d"


# ── handle_idle_txt ──────────────────────────────────────────────────────


def _ctx(samples=None):
    class FakeSampler:
        def snapshot(self):
            return samples or []
    return {"sampler": FakeSampler()}


def test_idle_txt_format_active():
    """ACTIVE state with util + power."""
    with patch.object(ip, "_gpus_available", return_value=[{"index": 0}]), \
         patch.object(ip, "_gpu_card_snapshot", return_value=_snap(util=85, power=250)):
        code, body = api.handle_idle_txt(_ctx())
    assert code == 200
    assert body.startswith("ACTIVE")
    assert "85%" in body
    assert "gpu0=250W" in body


def test_idle_txt_format_idle():
    with patch.object(ip, "_gpus_available", return_value=[{"index": 0}]), \
         patch.object(ip, "_gpu_card_snapshot", return_value=_snap(util=3, power=12)):
        code, body = api.handle_idle_txt(_ctx())
    assert body.startswith("IDLE")


def test_idle_txt_offline_card():
    with patch.object(ip, "_gpus_available", return_value=[{"index": 0}]), \
         patch.object(ip, "_gpu_card_snapshot", return_value={"alive": False}):
        code, body = api.handle_idle_txt(_ctx())
    assert "gpu0=off" in body
    assert body.startswith("IDLE")  # offline counts as idle


def test_idle_txt_multi_gpu_includes_each():
    def fake_snap(gpu_index=0, **kw):
        return _snap(util=10 + gpu_index * 5, power=20 + gpu_index * 10)
    with patch.object(ip, "_gpus_available",
                      return_value=[{"index": 0}, {"index": 1}]), \
         patch.object(ip, "_gpu_card_snapshot", side_effect=fake_snap):
        code, body = api.handle_idle_txt(_ctx())
    assert "gpu0=" in body
    assert "gpu1=" in body


def test_idle_txt_custom_thresholds():
    """Active under default thresholds → idle under more permissive ones."""
    with patch.object(ip, "_gpus_available", return_value=[{"index": 0}]), \
         patch.object(ip, "_gpu_card_snapshot", return_value=_snap(util=12, power=25)):
        # Default thresholds : util<15 AND power<30 → idle
        code, body = api.handle_idle_txt(_ctx())
        assert body.startswith("IDLE")
        # Tighter : util<5 OR power<10 → no longer idle
        code, body2 = api.handle_idle_txt(_ctx(), {"util_thresh": "5", "power_thresh": "10"})
        assert body2.startswith("ACTIVE")


def test_idle_txt_invalid_threshold_falls_back():
    with patch.object(ip, "_gpus_available", return_value=[{"index": 0}]), \
         patch.object(ip, "_gpu_card_snapshot", return_value=_snap(util=5, power=12)):
        code, body = api.handle_idle_txt(_ctx(), {"util_thresh": "not-a-number"})
    assert code == 200  # didn't crash
    # Default thresholds (15, 30) → still idle
    assert body.startswith("IDLE")


# ── _idle_since_seconds ──────────────────────────────────────────────────


def test_idle_since_walks_back_to_non_idle_sample():
    """5 idle samples after 1 active → since = ~5 sample intervals."""
    now = time.time()
    samples = [
        # oldest = active sample
        {"ts": now - 100, "util_gpu": 80, "power": 200},
        # then 5 idle samples
        {"ts": now - 80, "util_gpu": 5, "power": 15},
        {"ts": now - 60, "util_gpu": 4, "power": 14},
        {"ts": now - 40, "util_gpu": 3, "power": 13},
        {"ts": now - 20, "util_gpu": 2, "power": 12},
        {"ts": now - 5,  "util_gpu": 1, "power": 11},
    ]
    secs = ip._idle_since_seconds(_ctx(samples), True, 15, 30)
    # Should be ~100 seconds (since the oldest active)
    assert 95 < secs < 105


def test_idle_since_no_sampler_returns_none():
    assert ip._idle_since_seconds({}, True, 15, 30) is None


def test_idle_since_all_idle_returns_oldest_age():
    """No non-idle sample in buffer → return age of oldest sample."""
    now = time.time()
    samples = [
        {"ts": now - 300, "util_gpu": 2, "power": 12},
        {"ts": now - 200, "util_gpu": 1, "power": 11},
        {"ts": now - 100, "util_gpu": 0, "power": 10},
    ]
    secs = ip._idle_since_seconds(_ctx(samples), True, 15, 30)
    assert 250 < secs < 350


# ── handle_idle_json ─────────────────────────────────────────────────────


def test_idle_json_structured():
    with patch.object(ip, "_gpus_available", return_value=[{"index": 0}]), \
         patch.object(ip, "_gpu_card_snapshot", return_value=_snap(util=5, power=12)):
        code, body = api.handle_idle_json(_ctx())
    assert code == 200
    assert body["idle"] is True
    assert len(body["gpus"]) == 1
    assert body["util_thresh"] == 15.0
    assert body["power_thresh"] == 30.0
