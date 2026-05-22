"""R&D #12.1 — wall-meter Shelly/Tasmota bridge tests."""
import json
import pytest
from unittest.mock import patch, MagicMock
from gpu_dashboard.modules import wall_meter as wm
from gpu_dashboard.config import Config


class FakeResp:
    def __init__(self, status, body):
        self.status = status
        self._body = body.encode("utf-8") if isinstance(body, str) else body
    def __enter__(self): return self
    def __exit__(self, *a): pass
    def read(self): return self._body


# ── adapters ──────────────────────────────────────────────────────────────


def test_shelly1_parses_meters_power():
    body = json.dumps({"meters": [{"power": 123.4, "total": 456}]})
    with patch("urllib.request.urlopen", return_value=FakeResp(200, body)):
        w = wm._probe_shelly1("http://x")
    assert w == 123.4


def test_shelly_plus_parses_apower():
    body = json.dumps({"apower": 89.7, "voltage": 230})
    with patch("urllib.request.urlopen", return_value=FakeResp(200, body)):
        w = wm._probe_shelly_plus("http://x")
    assert w == 89.7


def test_tasmota_parses_status8():
    body = json.dumps({"StatusSNS": {"ENERGY": {"Power": 250.5, "Voltage": 230}}})
    with patch("urllib.request.urlopen", return_value=FakeResp(200, body)):
        w = wm._probe_tasmota("http://x")
    assert w == 250.5


def test_probe_returns_none_on_connection_error():
    with patch("urllib.request.urlopen", side_effect=ConnectionRefusedError):
        assert wm._probe_shelly1("http://x") is None
        assert wm._probe_shelly_plus("http://x") is None
        assert wm._probe_tasmota("http://x") is None


def test_shelly1_returns_none_on_empty_meters():
    body = json.dumps({"meters": []})
    with patch("urllib.request.urlopen", return_value=FakeResp(200, body)):
        assert wm._probe_shelly1("http://x") is None


def test_probe_dispatch_unknown_kind_returns_none():
    assert wm.probe("imaginary_brand", "http://x") is None


def test_kinds_supported_lists_all():
    k = wm.kinds_supported()
    assert "shelly1" in k
    assert "shelly_plus" in k
    assert "tasmota" in k


# ── efficiency ────────────────────────────────────────────────────────────


def test_efficiency_typical_case():
    """GPU 250W, wall 300W, baseline 35W → headroom 265W → eff = 250/265 ≈ 94%."""
    e = wm.efficiency(gpu_w=250, wall_w=300, baseline_w=35)
    assert e is not None
    assert 0.93 < e < 0.95


def test_efficiency_zero_headroom_returns_none():
    """If baseline >= wall, can't compute efficiency."""
    assert wm.efficiency(gpu_w=100, wall_w=30, baseline_w=35) is None


def test_efficiency_clamped_to_one():
    """If gpu_w somehow exceeds headroom (sampling skew), clamp to 1.0."""
    e = wm.efficiency(gpu_w=300, wall_w=300, baseline_w=35)
    assert e == 1.0


# ── status() top-level ────────────────────────────────────────────────────


def test_status_no_url_configured():
    cfg = Config(defaults={})
    r = wm.status(cfg)
    assert r["available"] is False
    assert "WALL_METER_URL" in r["reason"]


def test_status_unknown_kind():
    cfg = Config(defaults={"WALL_METER_URL": "http://x", "WALL_METER_KIND": "fake"})
    r = wm.status(cfg)
    assert r["available"] is False
    assert "unknown kind" in r["reason"]


def test_status_probe_fails():
    cfg = Config(defaults={"WALL_METER_URL": "http://x", "WALL_METER_KIND": "shelly1"})
    with patch.object(wm, "probe", return_value=None):
        r = wm.status(cfg)
    assert r["available"] is False
    assert "probe failed" in r["reason"]


def test_status_success_with_gpu_efficiency():
    cfg = Config(defaults={"WALL_METER_URL": "http://x", "WALL_METER_KIND": "shelly1",
                            "WALL_METER_BASELINE_W": "35"})
    with patch.object(wm, "probe", return_value=300.0):
        r = wm.status(cfg, gpu_w=250.0)
    assert r["available"] is True
    assert r["wall_w"] == 300.0
    assert r["baseline_w"] == 35.0
    assert r["headroom_w"] == 265.0
    assert r["gpu_w"] == 250.0
    assert r["psu_efficiency_pct"] is not None
    assert 93 < r["psu_efficiency_pct"] < 95


def test_status_handles_invalid_baseline():
    """Non-numeric baseline falls back to 35.0."""
    cfg = Config(defaults={"WALL_METER_URL": "http://x", "WALL_METER_KIND": "shelly1",
                            "WALL_METER_BASELINE_W": "not-a-number"})
    with patch.object(wm, "probe", return_value=300.0):
        r = wm.status(cfg, gpu_w=100.0)
    assert r["baseline_w"] == 35.0
