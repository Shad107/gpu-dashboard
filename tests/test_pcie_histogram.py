"""R&D #18.6 — PCIe link-state thrasher histogram tests."""
import time
import pytest
from unittest.mock import patch
from gpu_dashboard.modules import pcie_histogram as ph


# ── link_bucket ────────────────────────────────────────────────────────


def test_bucket_gen3_x16():
    assert ph.link_bucket(8.0, 16) == "Gen3 x16"


def test_bucket_gen4_x8():
    assert ph.link_bucket(16.0, 8) == "Gen4 x8"


def test_bucket_gen5_x16():
    assert ph.link_bucket(32.0, 16) == "Gen5 x16"


def test_bucket_unknown_speed():
    assert ph.link_bucket(None, 16) == "unknown"


def test_bucket_unknown_width():
    assert ph.link_bucket(8.0, None) == "unknown"


def test_bucket_offspec_speed_falls_through():
    out = ph.link_bucket(99.9, 16)
    assert "99.9" in out and "x16" in out


# ── _parse_link_str ────────────────────────────────────────────────────


def test_parse_link_str_typical():
    assert ph._parse_link_str("8.0 GT/s PCIe") == 8.0


def test_parse_link_str_gen5():
    assert ph._parse_link_str("32.0 GT/s PCIe") == 32.0


def test_parse_link_str_empty():
    assert ph._parse_link_str("") is None
    assert ph._parse_link_str(None) is None


def test_parse_width_int():
    assert ph._parse_width("16") == 16


def test_parse_width_bad():
    assert ph._parse_width("abc") is None
    assert ph._parse_width(None) is None


# ── build_histogram ────────────────────────────────────────────────────


def _ev(ts, before_speed="8.0 GT/s PCIe", before_w="16",
        after_speed="16.0 GT/s PCIe", after_w="16"):
    return {
        "kind": "link_change", "ts": ts, "target": "0000:01:00.0",
        "before": {"speed": before_speed, "width": before_w},
        "after":  {"speed": after_speed, "width": after_w},
    }


def test_histogram_empty():
    h = ph.build_histogram([], now_ts=1000)
    assert h["transition_count"] == 0
    assert h["verdict"] == "stable"
    assert h["buckets"] == {}


def test_histogram_one_transition():
    events = [_ev(950)]
    h = ph.build_histogram(events, window_s=3600, now_ts=1000)
    assert h["transition_count"] == 1
    assert h["buckets"] == {"Gen4 x16": 1}


def test_histogram_old_events_excluded():
    events = [_ev(100)]  # 900s before now=1000
    h = ph.build_histogram(events, window_s=500, now_ts=1000)
    assert h["transition_count"] == 0


def test_histogram_thrashing_verdict():
    # 120 transitions in last hour → 2 / min → thrashing
    events = [_ev(1000 - i * 30) for i in range(120)]
    h = ph.build_histogram(events, window_s=3600, now_ts=1000)
    assert h["transitions_per_min"] >= 1.0
    assert h["verdict"] == "thrashing"


def test_histogram_intermittent_verdict():
    # ~30 transitions in last hour → ~0.5 / min → intermittent
    events = [_ev(1000 - i * 120) for i in range(30)]
    h = ph.build_histogram(events, window_s=3600, now_ts=1000)
    assert h["verdict"] == "intermittent"


def test_histogram_stable_verdict():
    # 1 transition over an hour → 0.017 / min → stable
    events = [_ev(950)]
    h = ph.build_histogram(events, window_s=3600, now_ts=1000)
    assert h["verdict"] == "stable"


def test_histogram_first_last_ts():
    events = [_ev(900), _ev(950), _ev(990)]
    h = ph.build_histogram(events, window_s=3600, now_ts=1000)
    assert h["first_event_ts"] == 900
    assert h["last_event_ts"] == 990


def test_histogram_ignores_non_link_events():
    events = [
        _ev(950),
        {"kind": "disconnect", "ts": 970, "target": "x"},
    ]
    h = ph.build_histogram(events, window_s=3600, now_ts=1000)
    assert h["transition_count"] == 1


def test_histogram_mixed_buckets():
    events = [
        _ev(900, after_speed="16.0 GT/s PCIe", after_w="16"),  # Gen4 x16
        _ev(920, after_speed="8.0 GT/s PCIe", after_w="16"),   # Gen3 x16
        _ev(950, after_speed="8.0 GT/s PCIe", after_w="8"),    # Gen3 x8
    ]
    h = ph.build_histogram(events, window_s=3600, now_ts=1000)
    assert h["buckets"]["Gen4 x16"] == 1
    assert h["buckets"]["Gen3 x16"] == 1
    assert h["buckets"]["Gen3 x8"] == 1


# ── status integration ─────────────────────────────────────────────────


def test_status_pulls_from_hot_swap():
    fake_state = {"events": [_ev(int(time.time()) - 60)]}
    with patch("gpu_dashboard.modules.hot_swap.load_state",
               return_value=fake_state):
        s = ph.status()
    assert s["ok"] is True
    assert s["histogram_1h"]["transition_count"] == 1
    assert s["histogram_24h"]["transition_count"] == 1


def test_status_handles_no_state():
    with patch("gpu_dashboard.modules.hot_swap.load_state",
               return_value={}):
        s = ph.status()
    assert s["histogram_1h"]["transition_count"] == 0
    assert s["total_events_seen"] == 0
