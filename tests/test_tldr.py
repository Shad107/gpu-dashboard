"""R&D #10.6 — ANSI/tldr endpoint tests."""
from unittest.mock import patch
from gpu_dashboard import api
from gpu_dashboard.config import Config


def _ctx(samples=None):
    class _S:
        def snapshot(self): return samples or []
    return {"config": Config(defaults={}), "sampler": _S()}


def _snap(temp=50, util=20, power=80, plim=350, name="NVIDIA GeForce RTX 3090"):
    return {
        "alive": True, "name": name, "temp": temp,
        "util_gpu": util, "power": power, "power_limit": plim,
        "mem_used_mib": 8192, "mem_total_mib": 24576,
        "pcie_gen": 4, "pcie_width": 16,
    }


def test_color_helper_disabled_returns_plain():
    assert api._color("hello", "red", enabled=False) == "hello"


def test_color_helper_enabled_wraps_ansi():
    out = api._color("hello", "red", enabled=True)
    assert "\x1b[31m" in out
    assert "\x1b[0m" in out
    assert "hello" in out


def test_temp_color_thresholds():
    assert api._temp_color(85) == "red"
    assert api._temp_color(75) == "yellow"
    assert api._temp_color(60) == "green"
    assert api._temp_color(30) == "cyan"


def test_spark_empty_input():
    assert api._spark([]) == ""


def test_spark_returns_unicode_blocks():
    out = api._spark([0, 50, 100], width=3)
    # Expect 3 chars : low, mid, high
    assert len(out) == 3
    blocks = " ▁▂▃▄▅▆▇█"
    for c in out:
        assert c in blocks


def test_tldr_offline_gpu_short_message():
    with patch.object(api._core, "_gpu_card_snapshot", return_value={"alive": False}):
        code, body = api.handle_tldr(_ctx())
    assert code == 200
    assert "offline" in body.lower()


def test_tldr_default_format_3_lines():
    with patch.object(api._core, "_gpu_card_snapshot", return_value=_snap()):
        code, body = api.handle_tldr(_ctx())
    lines = body.rstrip("\n").split("\n")
    # default 'tldr' format = 3 lines
    assert len(lines) == 3
    assert "GreenWatts" in lines[0]
    assert "RTX 3090" in lines[0]  # short name


def test_tldr_oneline_format():
    with patch.object(api._core, "_gpu_card_snapshot", return_value=_snap(50, 20, 80)):
        code, body = api.handle_tldr(_ctx(), {"fmt": "oneline"})
    # one line, no carriage at start
    lines = body.rstrip("\n").split("\n")
    assert len(lines) == 1
    assert "50°C" in lines[0]
    assert "20%" in lines[0]
    assert "80W" in lines[0]


def test_tldr_full_format_includes_pcie():
    with patch.object(api._core, "_gpu_card_snapshot", return_value=_snap()):
        code, body = api.handle_tldr(_ctx(), {"fmt": "full"})
    assert "Temperature" in body
    assert "VRAM" in body
    assert "PCIe" in body
    assert "Gen 4" in body


def test_tldr_no_color_header_suppresses_ansi():
    with patch.object(api._core, "_gpu_card_snapshot", return_value=_snap()):
        code, body = api.handle_tldr(_ctx(), headers={"NO_COLOR": "1"})
    # No escape sequences
    assert "\x1b[" not in body


def test_tldr_color_on_by_default():
    with patch.object(api._core, "_gpu_card_snapshot", return_value=_snap()):
        code, body = api.handle_tldr(_ctx())
    assert "\x1b[" in body  # ANSI codes present


def test_tldr_cols_clamped_to_range():
    """cols param clamped to [40, 200] in full mode."""
    with patch.object(api._core, "_gpu_card_snapshot", return_value=_snap()):
        # cols=10 should clamp to 40 → at least 40 dashes in separator
        code, body = api.handle_tldr(_ctx(), {"fmt": "full", "cols": "10"})
    # Separator line has at least 40 dashes
    assert "─" * 40 in body


def test_tldr_sparkline_from_sampler_history():
    """When sampler has util history, the sparkline shows up."""
    samples = [{"util_gpu": i * 10} for i in range(20)]
    with patch.object(api._core, "_gpu_card_snapshot", return_value=_snap()):
        code, body = api.handle_tldr(_ctx(samples))
    # sparkline = unicode blocks
    blocks = "▁▂▃▄▅▆▇█"
    assert any(b in body for b in blocks)
