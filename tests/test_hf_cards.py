"""R&D #10.3 — HF model card cross-ref tests."""
import json
import os
import tempfile
import time
from unittest.mock import patch
from gpu_dashboard.modules import hf_cards as hc


def _with_tmp_cache(td):
    return patch.object(hc, "cache_path", return_value=os.path.join(td, "hf.json"))


# ── parse_repo_from_path ─────────────────────────────────────────────────


def test_parse_repo_from_hf_cache_path():
    p = "~/.cache/huggingface/hub/models--Qwen--Qwen2.5-7B-Instruct-GGUF/blobs/abc"
    assert hc.parse_repo_from_path(p) == "Qwen/Qwen2.5-7B-Instruct-GGUF"


def test_parse_repo_from_bare_repo_id():
    """'Qwen/Qwen2.5-7B' is already a repo id."""
    assert hc.parse_repo_from_path("Qwen/Qwen2.5-7B") == "Qwen/Qwen2.5-7B"


def test_parse_repo_from_local_gguf_returns_none():
    """Local file path with no HF cache markers → None (can't guess org)."""
    assert hc.parse_repo_from_path("/home/u/models/random.gguf") is None


def test_parse_repo_empty_input():
    assert hc.parse_repo_from_path("") is None
    assert hc.parse_repo_from_path(None) is None


# ── normalize ───────────────────────────────────────────────────────────


def test_normalize_extracts_license_and_base_model():
    raw = {
        "modelId": "Qwen/Qwen2.5-7B",
        "author": "Qwen",
        "downloads": 80346,
        "likes": 142,
        "cardData": {
            "license": "apache-2.0",
            "base_model": "Qwen/Qwen2.5-7B-Base",
            "license_link": "https://...",
            "pipeline_tag": "text-generation",
        },
        "lastModified": "2026-01-01T00:00:00Z",
        "tags": ["llm", "qwen"],
    }
    n = hc.normalize(raw)
    assert n["id"] == "Qwen/Qwen2.5-7B"
    assert n["license"] == "apache-2.0"
    assert n["base_model"] == "Qwen/Qwen2.5-7B-Base"
    assert n["downloads"] == 80346
    assert "fetched_ts" in n


def test_normalize_base_model_as_list_uses_first():
    raw = {"cardData": {"base_model": ["org/m1", "org/m2"]}}
    assert hc.normalize(raw)["base_model"] == "org/m1"


def test_normalize_missing_card_data():
    """When cardData is missing or non-dict, normalize still works."""
    n = hc.normalize({"modelId": "X/Y"})
    assert n["id"] == "X/Y"
    assert n["license"] is None


# ── license_color ───────────────────────────────────────────────────────


def test_license_color_permissive_green():
    assert hc.license_color("MIT") == "#4c1"
    assert hc.license_color("apache-2.0") == "#4c1"
    assert hc.license_color("BSD-3-Clause") == "#4c1"


def test_license_color_copyleft_yellow():
    assert hc.license_color("GPL-3.0") == "#dfb317"
    assert hc.license_color("agpl-3.0") == "#dfb317"


def test_license_color_restrictive_red():
    assert hc.license_color("llama-3-community") == "#e05d44"
    assert hc.license_color("cc-by-nc-4.0") == "#e05d44"
    assert hc.license_color("openrail") == "#e05d44"


def test_license_color_none_returns_gray():
    assert hc.license_color(None) == "#9f9f9f"
    assert hc.license_color("") == "#9f9f9f"


def test_license_color_unknown_returns_blue():
    assert hc.license_color("custom-weird-license") == "#007ec6"


# ── get_card with cache ─────────────────────────────────────────────────


def test_get_card_fresh_cache_returns_cached():
    with tempfile.TemporaryDirectory() as td, _with_tmp_cache(td):
        cache = {"X/Y": {"id": "X/Y", "license": "MIT", "fetched_ts": int(time.time())}}
        hc.save_cache(cache)
        # Don't call fetch — cache is fresh
        with patch.object(hc, "fetch_card", side_effect=AssertionError("should NOT fetch")):
            result = hc.get_card("X/Y")
        assert result["license"] == "MIT"


def test_get_card_stale_cache_fetches_fresh():
    with tempfile.TemporaryDirectory() as td, _with_tmp_cache(td):
        old_ts = int(time.time()) - 10 * 86400  # 10 days old
        cache = {"X/Y": {"id": "X/Y", "license": "OLD", "fetched_ts": old_ts}}
        hc.save_cache(cache)
        new_data = {"id": "X/Y", "license": "NEW", "fetched_ts": int(time.time())}
        with patch.object(hc, "fetch_card", return_value=new_data):
            result = hc.get_card("X/Y")
        assert result["license"] == "NEW"


def test_get_card_network_failure_returns_stale():
    """Offline-first : if fetch fails and stale cache exists, return stale."""
    with tempfile.TemporaryDirectory() as td, _with_tmp_cache(td):
        old_ts = int(time.time()) - 10 * 86400
        cache = {"X/Y": {"id": "X/Y", "license": "STALE", "fetched_ts": old_ts}}
        hc.save_cache(cache)
        with patch.object(hc, "fetch_card", return_value=None):
            result = hc.get_card("X/Y")
        assert result["license"] == "STALE"  # better than nothing


def test_get_card_no_cache_no_network_returns_none():
    with tempfile.TemporaryDirectory() as td, _with_tmp_cache(td):
        with patch.object(hc, "fetch_card", return_value=None):
            assert hc.get_card("Unknown/Repo") is None
