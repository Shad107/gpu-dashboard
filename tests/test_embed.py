"""R&D #12.6 — iframe embed view tests."""
import pytest
from unittest.mock import patch
from gpu_dashboard import api
from gpu_dashboard.api import embed
from gpu_dashboard.config import Config


def _snap(temp=50, util=20, power=80, plim=350, name="NVIDIA GeForce RTX 3090"):
    return {
        "alive": True, "name": name, "temp": temp,
        "util_gpu": util, "power": power, "power_limit": plim,
        "mem_used_mib": 8192, "mem_total_mib": 24576,
    }


def _ctx(require_token: bool = False):
    cfg = Config(defaults={"EMBED_REQUIRE_TOKEN": "1" if require_token else "0"})
    return {"config": cfg}


def test_handle_embed_unknown_card_returns_404():
    code, body = api.handle_embed(_ctx(), "weird")
    assert code == 404
    assert "unknown card" in body


def test_handle_embed_summary_default():
    with patch.object(embed._m, "_gpu_card_snapshot", return_value=_snap()):
        code, body = api.handle_embed(_ctx(), "summary")
    assert code == 200
    assert "Temperature" in body
    assert "Power" in body
    assert "Utilization" in body
    assert "VRAM" in body


def test_handle_embed_offline_shows_message():
    with patch.object(embed._m, "_gpu_card_snapshot", return_value={"alive": False}):
        code, body = api.handle_embed(_ctx(), "summary")
    assert code == 200
    assert "GPU offline" in body


def test_handle_embed_temp_only_card():
    with patch.object(embed._m, "_gpu_card_snapshot", return_value=_snap(temp=42)):
        code, body = api.handle_embed(_ctx(), "temp")
    assert "Temperature" in body
    assert "42°C" in body
    # The single-card embed should NOT include other cards
    assert "Utilization" not in body
    assert "VRAM" not in body


def test_handle_embed_temp_color_thresholds():
    """Hot temp picks red ; cool picks blue/green."""
    # Hot
    with patch.object(embed._m, "_gpu_card_snapshot", return_value=_snap(temp=85)):
        _, body = api.handle_embed(_ctx(), "temp")
    assert "#e05d44" in body  # red
    # Cool
    with patch.object(embed._m, "_gpu_card_snapshot", return_value=_snap(temp=40)):
        _, body = api.handle_embed(_ctx(), "temp")
    assert "#007ec6" in body  # cool blue


def test_handle_embed_requires_token_when_configured():
    """If EMBED_REQUIRE_TOKEN=1, unsigned requests get 401."""
    with patch.object(embed._m, "_gpu_card_snapshot", return_value=_snap()):
        code, body = api.handle_embed(_ctx(require_token=True), "summary")
    assert code == 401


def test_handle_embed_accepts_valid_share_token():
    """A valid share-link unlocks the embed."""
    import tempfile, os
    from gpu_dashboard.modules import auth_tokens as at
    with tempfile.TemporaryDirectory() as td:
        # Sandbox the server secret + verify share-link
        with patch.object(at, "tokens_path", return_value=os.path.join(td, "t.json")), \
             patch.object(at, "server_secret_path", return_value=os.path.join(td, ".s")):
            link = at.make_share_link(scope="read", ttl_s=3600, sub="alice")
            with patch.object(embed._m, "_gpu_card_snapshot", return_value=_snap()):
                code, body = api.handle_embed(
                    _ctx(require_token=True), "summary", {"share": link},
                )
    assert code == 200
    assert "shared by alice" in body


def test_handle_embed_invalid_share_token_rejected():
    code, body = api.handle_embed(_ctx(require_token=True), "summary",
                                  {"share": "bogus.token"})
    assert code == 401


def test_handle_embed_refresh_meta_present():
    with patch.object(embed._m, "_gpu_card_snapshot", return_value=_snap()):
        code, body = api.handle_embed(_ctx(), "summary", {"refresh": "60"})
    assert 'http-equiv="refresh"' in body
    assert 'content="60"' in body


def test_handle_embed_refresh_clamps_to_min():
    """refresh below 5s is clamped to 5s (avoid hammering server)."""
    with patch.object(embed._m, "_gpu_card_snapshot", return_value=_snap()):
        _, body = api.handle_embed(_ctx(), "summary", {"refresh": "1"})
    assert 'content="5"' in body


def test_handle_embed_theme_light():
    with patch.object(embed._m, "_gpu_card_snapshot", return_value=_snap()):
        _, body = api.handle_embed(_ctx(), "summary", {"theme": "light"})
    assert "#fafafa" in body  # light bg


def test_handle_embed_html_escapes_user_input():
    """Card slugs are user-controlled — must be HTML-escaped to prevent XSS."""
    code, body = api.handle_embed(_ctx(), "<script>alert(1)</script>")
    assert code == 404
    assert "<script>" not in body or "&lt;script&gt;" in body
