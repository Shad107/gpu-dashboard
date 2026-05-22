"""R&D #16.6 — NOC board tests."""
import pytest
from unittest.mock import patch
from gpu_dashboard import api
from gpu_dashboard.api import noc


def _snap(temp=50, util=20, power=80, name="NVIDIA GeForce RTX 3090", alive=True):
    return {
        "alive": alive, "name": name, "temp": temp,
        "util_gpu": util, "power": power, "power_limit": 350,
        "mem_used_mib": 8192, "mem_total_mib": 24576,
    }


# ── _verdict ──────────────────────────────────────────────────────────────


def test_verdict_ok():
    assert noc._verdict(temp=50, util=30) == "ok"


def test_verdict_warn_high_temp():
    assert noc._verdict(temp=78, util=10) == "warn"


def test_verdict_warn_high_util():
    assert noc._verdict(temp=50, util=98) == "warn"


def test_verdict_crit():
    assert noc._verdict(temp=87, util=30) == "crit"


# ── _render_tile ─────────────────────────────────────────────────────────


def test_render_tile_offline_card():
    html = noc._render_tile({"alive": False}, noc._COLORS)
    assert "GPU offline" in html
    assert noc._COLORS["off"]["bg"] in html


def test_render_tile_includes_temp_and_util():
    html = noc._render_tile(_snap(temp=65, util=40), noc._COLORS)
    assert "65°C" in html
    assert "40% util" in html
    assert "RTX 3090" in html


def test_render_tile_uses_warn_palette_when_hot():
    html = noc._render_tile(_snap(temp=80), noc._COLORS)
    assert noc._COLORS["warn"]["bg"] in html


def test_render_tile_uses_crit_palette_when_overheating():
    html = noc._render_tile(_snap(temp=88), noc._COLORS)
    assert noc._COLORS["crit"]["bg"] in html


def test_render_tile_escapes_gpu_name():
    # GPU name with HTML-special chars must be escaped (defense-in-depth)
    snap = _snap(name="<script>X</script>")
    html = noc._render_tile(snap, noc._COLORS)
    assert "<script>" not in html
    assert "&lt;script&gt;" in html


# ── handle_noc top-level ─────────────────────────────────────────────────


def test_handle_noc_returns_html():
    with patch.object(noc, "_gpus_available", return_value=[{"index": 0}]), \
         patch.object(noc, "_gpu_card_snapshot", return_value=_snap()):
        code, body = api.handle_noc({})
    assert code == 200
    assert body.startswith("<!DOCTYPE html>")
    assert "<title>GreenWatts · NOC</title>" in body


def test_handle_noc_refresh_meta_present():
    with patch.object(noc, "_gpus_available", return_value=[{"index": 0}]), \
         patch.object(noc, "_gpu_card_snapshot", return_value=_snap()):
        code, body = api.handle_noc({}, {"refresh": "10"})
    assert 'http-equiv="refresh"' in body
    assert 'content="10"' in body


def test_handle_noc_refresh_clamps():
    """refresh < 2 → clamped to 2."""
    with patch.object(noc, "_gpus_available", return_value=[{"index": 0}]), \
         patch.object(noc, "_gpu_card_snapshot", return_value=_snap()):
        code, body = api.handle_noc({}, {"refresh": "0"})
    assert 'content="2"' in body


def test_handle_noc_no_gpus_shows_offline_placeholder():
    with patch.object(noc, "_gpus_available", return_value=[]):
        code, body = api.handle_noc({})
    assert "GPU offline" in body


def test_handle_noc_auto_picks_cols_for_4_gpus():
    """1 GPU → 1 col, 2-4 → 2 cols, 5-9 → 3."""
    def fake_snap(gpu_index=0, **kw):
        return _snap(temp=50 + gpu_index * 5)
    gpus = [{"index": i} for i in range(4)]
    with patch.object(noc, "_gpus_available", return_value=gpus), \
         patch.object(noc, "_gpu_card_snapshot", side_effect=fake_snap):
        code, body = api.handle_noc({})
    # 4 GPUs → 2 cols
    assert "grid-template-columns:repeat(2," in body


def test_handle_noc_auto_picks_cols_for_8_gpus():
    def fake_snap(gpu_index=0, **kw):
        return _snap()
    gpus = [{"index": i} for i in range(8)]
    with patch.object(noc, "_gpus_available", return_value=gpus), \
         patch.object(noc, "_gpu_card_snapshot", side_effect=fake_snap):
        code, body = api.handle_noc({})
    # 5-9 GPUs → 3 cols
    assert "grid-template-columns:repeat(3," in body


def test_handle_noc_explicit_cols_param():
    with patch.object(noc, "_gpus_available", return_value=[{"index": 0}]), \
         patch.object(noc, "_gpu_card_snapshot", return_value=_snap()):
        code, body = api.handle_noc({}, {"cols": "6"})
    assert "grid-template-columns:repeat(6," in body


def test_handle_noc_renders_one_tile_per_gpu():
    gpus = [{"index": 0}, {"index": 1}, {"index": 2}]
    with patch.object(noc, "_gpus_available", return_value=gpus), \
         patch.object(noc, "_gpu_card_snapshot", side_effect=lambda gpu_index=0, **kw: _snap(temp=50 + gpu_index * 5)):
        code, body = api.handle_noc({})
    # 3 tiles → 3 occurrences of noc-tile
    assert body.count('class="noc-tile"') == 3
