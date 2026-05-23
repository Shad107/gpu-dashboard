"""R&D #23.1 — Batch-size / ctx-length advisor tests."""
import pytest
from unittest.mock import patch
from gpu_dashboard.modules import batch_advisor as ba


# ── kv_cache_per_token_bytes ───────────────────────────────────────────


def test_kv_default_no_gqa():
    """2 * n_layers * n_embd * 2 bytes (fp16)."""
    out = ba.kv_cache_per_token_bytes(n_layers=32, n_embd=4096)
    assert out == 2 * 32 * 4096 * 2


def test_kv_int_dtype():
    out = ba.kv_cache_per_token_bytes(n_layers=8, n_embd=512, dtype_bytes=1)
    assert out == 2 * 8 * 512 * 1


def test_kv_with_n_kv_heads():
    """Heuristic estimate when n_kv_heads is provided."""
    out = ba.kv_cache_per_token_bytes(n_layers=32, n_embd=4096, n_kv_heads=8)
    # head_dim = 4096 // (8 * 2) = 256
    # 2 * 32 * 8 * 256 * 2 = 262144
    assert out == 2 * 32 * 8 * 256 * 2


# ── _infer_n_layers ────────────────────────────────────────────────────


def test_infer_n_layers_llama_7b():
    """LLaMA-7B has 32 layers, n_embd 4096, ~6.7B params."""
    n = ba._infer_n_layers(n_params=6_700_000_000, n_embd=4096)
    # 6.7e9 / (12 * 4096^2) ≈ 33.3 → 33
    assert n is not None
    assert 25 < n < 45


def test_infer_n_layers_zero_inputs():
    assert ba._infer_n_layers(0, 4096) is None
    assert ba._infer_n_layers(1000, 0) is None


def test_infer_n_layers_too_small():
    """If raw < 1, return None."""
    assert ba._infer_n_layers(1, 4096) is None


# ── compute_advisory ──────────────────────────────────────────────────


def test_advisory_zero_inputs():
    out = ba.compute_advisory(0, 0, 0, 0, 0)
    assert out["recommendation"].startswith("Not enough info")


def test_advisory_no_headroom():
    """Model takes all of VRAM → 0 headroom."""
    out = ba.compute_advisory(
        model_size_bytes=20 * 1024 ** 3,
        n_layers=32, n_embd=4096, n_ctx_train=4096,
        free_vram_bytes=20 * 1024 ** 3,
    )
    assert out["headroom_bytes"] == 0
    assert out["max_ctx_at_batch"] == 0
    assert "No headroom" in out["recommendation"]


def test_advisory_plenty_of_headroom():
    out = ba.compute_advisory(
        model_size_bytes=5 * 1024 ** 3,
        n_layers=32, n_embd=4096, n_ctx_train=4096,
        free_vram_bytes=24 * 1024 ** 3,
    )
    assert out["max_ctx_at_batch"] > 4096
    assert "full training context" in out["recommendation"]


def test_advisory_constrained_ctx():
    """Tight headroom → recommend a smaller ctx."""
    out = ba.compute_advisory(
        model_size_bytes=14 * 1024 ** 3,
        n_layers=80, n_embd=8192, n_ctx_train=32768,
        free_vram_bytes=24 * 1024 ** 3,
    )
    assert 0 < out["max_ctx_at_batch"] < 32768
    assert "Cap context" in out["recommendation"]


def test_advisory_batch_scaling():
    """Doubling batch should roughly halve max_ctx."""
    a = ba.compute_advisory(
        model_size_bytes=5 * 1024 ** 3,
        n_layers=32, n_embd=4096, n_ctx_train=4096,
        free_vram_bytes=24 * 1024 ** 3,
        target_batch=1,
    )
    b = ba.compute_advisory(
        model_size_bytes=5 * 1024 ** 3,
        n_layers=32, n_embd=4096, n_ctx_train=4096,
        free_vram_bytes=24 * 1024 ** 3,
        target_batch=2,
    )
    assert abs(a["max_ctx_at_batch"] / max(1, b["max_ctx_at_batch"]) - 2) < 0.1


# ── status ────────────────────────────────────────────────────────────


def test_status_no_smi():
    with patch.object(ba, "query_gpu_vram", return_value=None):
        s = ba.status()
    assert s["ok"] is False
    assert "unreachable" in s["reason"]


def test_status_no_models():
    with patch.object(ba, "query_gpu_vram",
                      return_value={"total_mib": 24576,
                                     "used_mib": 100,
                                     "free_mib": 24476}):
        with patch.object(ba, "probe_llamaserver_models", return_value=[]):
            s = ba.status()
    assert s["ok"] is True
    assert s["advisors"] == []


def test_status_advisor_for_one_model():
    fake_model = {
        "id": "test-7b.gguf",
        "n_ctx_train": 4096,
        "n_params": 6_700_000_000,
        "size_bytes": 5 * 1024 ** 3,
        "n_embd": 4096,
        "n_vocab": 32000,
    }
    with patch.object(ba, "query_gpu_vram",
                      return_value={"total_mib": 24576,
                                     "used_mib": 100,
                                     "free_mib": 24476}):
        with patch.object(ba, "probe_llamaserver_models",
                          return_value=[fake_model]):
            s = ba.status()
    assert len(s["advisors"]) == 1
    adv = s["advisors"][0]
    assert adv["headroom_mib"] > 0
    assert adv["max_ctx_at_batch"] > 0


def test_status_skips_models_without_meta():
    fake_model = {"id": "partial.gguf", "n_ctx_train": None,
                   "n_params": None, "size_bytes": 0,
                   "n_embd": None, "n_vocab": None}
    with patch.object(ba, "query_gpu_vram",
                      return_value={"total_mib": 24576,
                                     "used_mib": 100,
                                     "free_mib": 24476}):
        with patch.object(ba, "probe_llamaserver_models",
                          return_value=[fake_model]):
            s = ba.status()
    assert s["advisors"] == []


def test_status_uses_batch_config():
    fake_model = {
        "id": "x", "n_ctx_train": 4096, "n_params": 7e9,
        "size_bytes": 5 * 1024 ** 3, "n_embd": 4096, "n_vocab": 32000,
    }
    with patch.object(ba, "query_gpu_vram",
                      return_value={"total_mib": 24576,
                                     "used_mib": 100,
                                     "free_mib": 24476}):
        with patch.object(ba, "probe_llamaserver_models",
                          return_value=[fake_model]):
            s = ba.status(cfg={"BATCH_ADVISOR_BATCH": "4"})
    assert s["target_batch"] == 4
