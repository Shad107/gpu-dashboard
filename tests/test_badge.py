"""R&D #10.7 — SVG badge generator tests."""
from unittest.mock import patch
from gpu_dashboard import api
from gpu_dashboard.config import Config


def _ctx():
    return {"config": Config(defaults={}), "sampler": None, "started_at": 0}


def _snap(temp=50, util=20, power=80, name="NVIDIA GeForce RTX 3090"):
    return {
        "alive": True, "name": name, "temp": temp,
        "util_gpu": util, "power": power, "power_limit": 350,
        "mem_used_mib": 8192, "mem_total_mib": 24576,
    }


def test_badge_svg_helper_includes_label_and_value():
    out = api._badge_svg("temp", "42°C", "#4c1")
    assert "<svg" in out
    assert "</svg>" in out
    assert "temp" in out
    assert "42°C" in out
    assert "#4c1" in out


def test_badge_gpu_temp_ok():
    """Temp < 70°C → green (#4c1)."""
    with patch.object(api, "_gpu_card_snapshot", return_value=_snap(temp=42)):
        code, svg = api.handle_badge(_ctx(), "gpu-temp")
    assert code == 200
    assert "#4c1" in svg
    assert "42°C" in svg


def test_badge_gpu_temp_warn():
    """70-80°C → yellow."""
    with patch.object(api, "_gpu_card_snapshot", return_value=_snap(temp=75)):
        code, svg = api.handle_badge(_ctx(), "gpu-temp")
    assert "#dfb317" in svg


def test_badge_gpu_temp_crit():
    """>= 80°C → red."""
    with patch.object(api, "_gpu_card_snapshot", return_value=_snap(temp=85)):
        code, svg = api.handle_badge(_ctx(), "gpu-temp")
    assert "#e05d44" in svg


def test_badge_offline_returns_gray():
    """When GPU is offline, badge shows 'offline' in gray."""
    with patch.object(api, "_gpu_card_snapshot", return_value={"alive": False}):
        code, svg = api.handle_badge(_ctx(), "gpu-temp")
    assert "offline" in svg
    assert "#9f9f9f" in svg


def test_badge_power_now_includes_watts():
    with patch.object(api, "_gpu_card_snapshot", return_value=_snap(power=240.5)):
        code, svg = api.handle_badge(_ctx(), "power-now")
    assert "241 W" in svg or "240 W" in svg
    assert "power" in svg


def test_badge_util_color_thresholds():
    """util 0-49 = green, 50-89 = yellow, 90+ = red."""
    with patch.object(api, "_gpu_card_snapshot", return_value=_snap(util=10)):
        _, svg = api.handle_badge(_ctx(), "util")
    assert "#4c1" in svg
    with patch.object(api, "_gpu_card_snapshot", return_value=_snap(util=70)):
        _, svg = api.handle_badge(_ctx(), "util")
    assert "#dfb317" in svg
    with patch.object(api, "_gpu_card_snapshot", return_value=_snap(util=95)):
        _, svg = api.handle_badge(_ctx(), "util")
    assert "#e05d44" in svg


def test_badge_uptime_formats():
    """uptime: <60s = Ns, <60min = Nm, <24h = Nh, else Nd."""
    import time
    now = time.time()
    ctx_5s = {"config": Config(defaults={}), "sampler": None, "started_at": now - 5}
    _, svg = api.handle_badge(ctx_5s, "uptime")
    assert "5s" in svg

    ctx_2m = {"config": Config(defaults={}), "sampler": None, "started_at": now - 130}
    _, svg = api.handle_badge(ctx_2m, "uptime")
    assert "2m" in svg

    ctx_3h = {"config": Config(defaults={}), "sampler": None, "started_at": now - 3 * 3600 - 100}
    _, svg = api.handle_badge(ctx_3h, "uptime")
    assert "3h" in svg

    ctx_2d = {"config": Config(defaults={}), "sampler": None, "started_at": now - 2 * 86400 - 500}
    _, svg = api.handle_badge(ctx_2d, "uptime")
    assert "2d" in svg


def test_badge_top_model_shows_short_name():
    with patch.object(api, "_gpu_card_snapshot", return_value=_snap(name="NVIDIA GeForce RTX 3090")):
        code, svg = api.handle_badge(_ctx(), "top-model")
    assert "RTX 3090" in svg
    # Should NOT contain the long prefix
    assert "NVIDIA GeForce" not in svg


def test_badge_unknown_returns_404_with_fallback_svg():
    code, svg = api.handle_badge(_ctx(), "lolnope")
    assert code == 404
    assert "<svg" in svg
    assert "unknown:lolnope" in svg


def test_badge_svg_includes_xmlns():
    """Must include xmlns for browser rendering."""
    out = api._badge_svg("k", "v", "#4c1")
    assert 'xmlns="http://www.w3.org/2000/svg"' in out
