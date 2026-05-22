"""R&D #16.7 — LM-Studio model bridge tests."""
import os
import struct
import tempfile
import pytest
from unittest.mock import patch
from gpu_dashboard.modules import lm_studio as lm


def _write_fake_gguf(path: str, padding_bytes: int = 0):
    """Write a minimal valid GGUF header followed by padding."""
    with open(path, "wb") as f:
        f.write(b"GGUF")
        f.write(struct.pack("<I", 3))     # version 3
        f.write(struct.pack("<Q", 200))   # 200 tensors
        f.write(struct.pack("<Q", 30))    # 30 metadata KV
        if padding_bytes > 0:
            f.write(b"\x00" * padding_bytes)


def _write_random(path: str, size: int = 1024):
    with open(path, "wb") as f:
        f.write(b"\x42" * size)


# ── parse_gguf_header ────────────────────────────────────────────────────


def test_parse_gguf_valid():
    with tempfile.TemporaryDirectory() as td:
        p = os.path.join(td, "model.gguf")
        _write_fake_gguf(p)
        h = lm.parse_gguf_header(p)
    assert h["is_gguf"] is True
    assert h["version"] == 3
    assert h["tensor_count"] == 200
    assert h["metadata_kv_count"] == 30


def test_parse_gguf_wrong_magic():
    with tempfile.TemporaryDirectory() as td:
        p = os.path.join(td, "fake.gguf")
        _write_random(p, 32)
        h = lm.parse_gguf_header(p)
    assert h["is_gguf"] is False
    assert "not a GGUF" in h["reason"]


def test_parse_gguf_missing_file():
    h = lm.parse_gguf_header("/does/not/exist")
    assert h["is_gguf"] is False


def test_parse_gguf_truncated():
    """File too short to contain the full header."""
    with tempfile.TemporaryDirectory() as td:
        p = os.path.join(td, "short.gguf")
        with open(p, "wb") as f:
            f.write(b"GGUF")  # just the magic
        h = lm.parse_gguf_header(p)
    assert h["is_gguf"] is False
    assert "truncated" in h["reason"]


# ── _infer_quant_from_name ───────────────────────────────────────────────


def test_infer_quant_q4_k_m():
    assert lm._infer_quant_from_name("Qwen2.5-7B-Q4_K_M.gguf") == "Q4_K_M"


def test_infer_quant_q8_0():
    assert lm._infer_quant_from_name("model-q8_0.gguf") == "Q8_0"


def test_infer_quant_unknown_returns_none():
    assert lm._infer_quant_from_name("random.gguf") is None


# ── scan_models ──────────────────────────────────────────────────────────


def test_scan_models_empty_dir():
    with tempfile.TemporaryDirectory() as td:
        assert lm.scan_models(td) == []


def test_scan_models_no_dir_returns_empty():
    assert lm.scan_models("/does/not/exist") == []


def test_scan_models_picks_up_gguf_files():
    with tempfile.TemporaryDirectory() as td:
        org = os.path.join(td, "lmstudio-community", "Qwen2.5-7B-GGUF")
        os.makedirs(org)
        p1 = os.path.join(org, "Qwen2.5-7B-Q4_K_M.gguf")
        _write_fake_gguf(p1)
        with open(os.path.join(org, "README.md"), "w") as f:
            f.write("hi")
        out = lm.scan_models(td)
    assert len(out) == 1
    assert out[0]["name"] == "Qwen2.5-7B-Q4_K_M.gguf"
    assert out[0]["is_gguf"] is True
    assert out[0]["quant"] == "Q4_K_M"
    assert out[0]["dir_top"] == "lmstudio-community"


def test_scan_models_walks_recursively():
    with tempfile.TemporaryDirectory() as td:
        a = os.path.join(td, "a", "deep", "nested")
        b = os.path.join(td, "b")
        os.makedirs(a)
        os.makedirs(b)
        _write_fake_gguf(os.path.join(a, "model1.gguf"))
        _write_fake_gguf(os.path.join(b, "model2.gguf"))
        out = lm.scan_models(td)
    assert len(out) == 2


# ── find_size_collisions ─────────────────────────────────────────────────


def test_find_size_collisions_returns_matches():
    """Two files with identical size in different roots → collision."""
    with tempfile.TemporaryDirectory() as td:
        lm_dir = os.path.join(td, "lm")
        hf_dir = os.path.join(td, "hf")
        os.makedirs(lm_dir)
        os.makedirs(hf_dir)
        lm_path = os.path.join(lm_dir, "model.gguf")
        hf_path = os.path.join(hf_dir, "blob")
        # Both files exactly 200 MiB
        with open(lm_path, "wb") as f:
            f.write(b"\x00" * (200 * 1024 * 1024))
        with open(hf_path, "wb") as f:
            f.write(b"\x00" * (200 * 1024 * 1024))
        lm_models = lm.scan_models(lm_dir)
        matches = lm.find_size_collisions(lm_models, hf_cache_dir=hf_dir)
    assert len(matches) == 1
    assert matches[0]["lm_path"] == lm_path
    assert hf_path in matches[0]["hf_candidates"]


def test_find_size_collisions_ignores_tiny_files():
    """Files < 100 MiB are skipped (too noisy for dedup matching)."""
    with tempfile.TemporaryDirectory() as td:
        lm_dir = os.path.join(td, "lm")
        hf_dir = os.path.join(td, "hf")
        os.makedirs(lm_dir)
        os.makedirs(hf_dir)
        with open(os.path.join(lm_dir, "small.gguf"), "wb") as f:
            f.write(b"\x00" * 1024)
        with open(os.path.join(hf_dir, "blob"), "wb") as f:
            f.write(b"\x00" * 1024)
        lm_models = lm.scan_models(lm_dir)
        matches = lm.find_size_collisions(lm_models, hf_cache_dir=hf_dir)
    assert matches == []


def test_find_size_collisions_no_hf_cache():
    """Missing HF cache dir → no matches, no exception."""
    with tempfile.TemporaryDirectory() as td:
        matches = lm.find_size_collisions([{"path": "x", "size_bytes": 999}],
                                            hf_cache_dir="/no/such")
    assert matches == []


# ── read_settings ────────────────────────────────────────────────────────


def test_read_settings_missing_returns_empty():
    with tempfile.TemporaryDirectory() as td:
        with patch.object(lm, "settings_path",
                          return_value=os.path.join(td, "missing.json")):
            assert lm.read_settings() == {}


def test_read_settings_parses_json():
    with tempfile.TemporaryDirectory() as td:
        p = os.path.join(td, "settings.json")
        with open(p, "w") as f:
            f.write('{"defaultModel": "Qwen2.5-7B", "contextLength": 4096}')
        with patch.object(lm, "settings_path", return_value=p):
            s = lm.read_settings()
    assert s["defaultModel"] == "Qwen2.5-7B"
    assert s["contextLength"] == 4096


# ── status ───────────────────────────────────────────────────────────────


def test_status_no_lm_studio_returns_unavailable():
    with tempfile.TemporaryDirectory() as td:
        with patch.object(lm, "models_dir",
                          return_value=os.path.join(td, "missing")):
            s = lm.status()
    assert s["available"] is False
    assert "LM-Studio" in s["reason"]


def test_status_aggregates_models_and_collisions():
    """End-to-end : scan + find collisions + total size + counts."""
    with tempfile.TemporaryDirectory() as td:
        lm_dir = os.path.join(td, "lm")
        hf_dir = os.path.join(td, "hf")
        os.makedirs(lm_dir)
        os.makedirs(hf_dir)
        p = os.path.join(lm_dir, "model-Q4_K_M.gguf")
        _write_fake_gguf(p, padding_bytes=200 * 1024 * 1024)
        with open(os.path.join(hf_dir, "blob"), "wb") as f:
            f.write(b"\x00" * os.path.getsize(p))   # identical size
        with patch.object(lm, "models_dir", return_value=lm_dir), \
             patch.object(lm, "_HF_CACHE_DIR", hf_dir):
            s = lm.status()
    assert s["available"] is True
    assert s["models_count"] == 1
    assert s["total_size_gib"] >= 0.1
    # HF cache size match should be detected → 1 dedup suspect
    assert s["duplication_suspect_count"] == 1
