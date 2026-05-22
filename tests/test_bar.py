"""R&D #5.4 — Bar JSON output for desktop bars (waybar, polybar, etc.)."""
import json
from unittest.mock import patch
from gpu_dashboard import api
from gpu_dashboard.config import Config


def _ctx():
    return {"config": Config(defaults={})}


def _snap(temp=50, util=20, power=80, name="RTX 3090"):
    return {
        "alive": True, "name": name, "temp": temp,
        "util_gpu": util, "power": power,
        "mem_used_mib": 8192, "mem_total_mib": 24576,
    }


def test_waybar_default_fmt():
    with patch.object(api, "_gpu_card_snapshot", return_value=_snap(50, 20, 80)):
        code, body = api.handle_bar(_ctx(), None)
    assert code == 200
    assert isinstance(body, dict)
    assert body["text"] == "50°C 20% 80W"
    assert body["class"] == "ok"
    assert body["percentage"] == 20
    assert "GPU :" in body["tooltip"]


def test_waybar_critical_temp_class():
    with patch.object(api, "_gpu_card_snapshot", return_value=_snap(86, 90, 320)):
        code, body = api.handle_bar(_ctx(), None)
    assert body["class"] == "critical"


def test_waybar_warning_temp_class():
    with patch.object(api, "_gpu_card_snapshot", return_value=_snap(78, 70, 250)):
        code, body = api.handle_bar(_ctx(), None)
    assert body["class"] == "warning"


def test_polybar_format_has_color_tags():
    with patch.object(api, "_gpu_card_snapshot", return_value=_snap(50, 20, 80)):
        code, body = api.handle_bar(_ctx(), {"fmt": "polybar"})
    assert code == 200
    assert isinstance(body, str)
    assert "%{F#" in body
    assert "50°C" in body
    assert "%{F-}" in body


def test_i3blocks_format_3_lines():
    with patch.object(api, "_gpu_card_snapshot", return_value=_snap(50, 20, 80)):
        code, body = api.handle_bar(_ctx(), {"fmt": "i3blocks"})
    lines = body.split("\n")
    assert len(lines) == 3
    assert "50°C" in lines[0]
    assert lines[1] == "50°"
    assert lines[2].startswith("#")  # color hex


def test_tmux_format_has_fg_tag():
    with patch.object(api, "_gpu_card_snapshot", return_value=_snap(50, 20, 80)):
        code, body = api.handle_bar(_ctx(), {"fmt": "tmux"})
    assert "#[fg=" in body
    assert "50°C" in body
    assert "#[default]" in body


def test_plain_format_no_markup():
    with patch.object(api, "_gpu_card_snapshot", return_value=_snap(50, 20, 80)):
        code, body = api.handle_bar(_ctx(), {"fmt": "plain"})
    assert body == "50°C 20% 80W"


def test_gpu_off_returns_off_status():
    with patch.object(api, "_gpu_card_snapshot", return_value={"alive": False}):
        code, body = api.handle_bar(_ctx(), None)
    assert body["text"] == "GPU N/A"
    assert body["class"] == "off"
