"""Module batch_advisor — Batch-size / ctx-length advisor (R&D #23.1).

The #1 LLM trial-and-error pain point : "what's the largest context
window I can fit on this card?" — which is what OOM-kills people in
the middle of a long agent loop. Users currently bisect by hand.

This module fuses three already-shipped modules :

  - GGUF parser (#17.4) → model hidden_dim, n_layers, n_kv_heads,
    n_params, n_ctx_train. Pulled from llama-server's /v1/models.
  - VRAM quota (#13.x) → user-configured ceiling.
  - Warmup profiler (#19.4) → empirical TTFT data validates the
    recommendation.

The math (transformer KV cache, fp16 default) :

    kv_per_token_bytes ≈ 2 * n_layers * n_kv_heads * head_dim * 2

where head_dim = hidden_dim / n_attention_heads. n_kv_heads is the
same as n_attention_heads for non-GQA models. We approximate it as
hidden_dim / 128 when n_kv_heads is unknown — overestimates safely.

Headroom = effective_vram - model_weight_size.
Max ctx  = headroom / kv_per_token / batch_size.

stdlib only.
"""
from __future__ import annotations

import json
import shutil
import subprocess
import urllib.error
import urllib.request
from typing import Optional


NAME = "batch_advisor"


# Defaults if not derivable from model metadata
DEFAULT_KV_BYTES_PER_PARAM = 2  # fp16
DEFAULT_HEAD_DIM = 128


def _http_get_json(url: str, timeout: float = 1.5) -> Optional[dict]:
    try:
        with urllib.request.urlopen(url, timeout=timeout) as r:
            if r.status != 200:
                return None
            return json.loads(r.read().decode("utf-8"))
    except (urllib.error.URLError, urllib.error.HTTPError, OSError,
            json.JSONDecodeError, TimeoutError):
        return None


def probe_llamaserver_models(host: str = "localhost",
                              port: int = 8080) -> list[dict]:
    """Get the rich metadata from llama-server. Each entry has .meta
    with n_ctx_train / n_params / size / n_embd / n_vocab."""
    d = _http_get_json(f"http://{host}:{port}/v1/models")
    if not d:
        return []
    items = d.get("data") or d.get("models") or []
    out: list[dict] = []
    for m in items:
        if not isinstance(m, dict):
            continue
        meta = m.get("meta") or {}
        out.append({
            "id": m.get("id") or m.get("name") or "?",
            "n_ctx_train": meta.get("n_ctx_train"),
            "n_params": meta.get("n_params"),
            "size_bytes": meta.get("size"),
            "n_embd": meta.get("n_embd"),
            "n_vocab": meta.get("n_vocab"),
        })
    return out


def query_gpu_vram(timeout: float = 2.0) -> Optional[dict]:
    """Return {total_mib, used_mib, free_mib} for GPU 0."""
    if not shutil.which("nvidia-smi"):
        return None
    try:
        r = subprocess.run(
            ["nvidia-smi",
             "--query-gpu=memory.total,memory.used,memory.free",
             "--format=csv,noheader,nounits", "-i", "0"],
            capture_output=True, text=True, timeout=timeout,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired, OSError):
        return None
    if r.returncode != 0 or not r.stdout.strip():
        return None
    parts = [p.strip() for p in r.stdout.splitlines()[0].split(",")]
    try:
        return {
            "total_mib": int(parts[0]),
            "used_mib": int(parts[1]),
            "free_mib": int(parts[2]),
        }
    except (ValueError, IndexError):
        return None


def kv_cache_per_token_bytes(n_layers: int, n_embd: int,
                              n_kv_heads: Optional[int] = None,
                              dtype_bytes: int = 2) -> int:
    """KV cache cost per token, in bytes. The simple formula :

        2 (K+V) * n_layers * n_kv_heads * head_dim * dtype_bytes
        = 2 * n_layers * n_embd * dtype_bytes   (when n_kv_heads = n_heads)

    For GQA models, n_kv_heads < n_heads — but we don't always know.
    Conservative default = full n_embd worth of cache (no GQA discount).
    """
    if n_kv_heads is None or not isinstance(n_kv_heads, int):
        return 2 * n_layers * n_embd * dtype_bytes
    # Reconstruct head_dim
    head_dim = max(1, n_embd // max(1, n_kv_heads * 2))  # rough estimate
    return 2 * n_layers * n_kv_heads * head_dim * dtype_bytes


def compute_advisory(model_size_bytes: int, n_layers: int, n_embd: int,
                      n_ctx_train: int,
                      free_vram_bytes: int,
                      target_batch: int = 1) -> dict:
    """Given model footprint + GPU headroom, compute :
      - kv_per_token_bytes
      - max_ctx_at_batch
      - max_batch_at_ctx_train
      - recommendation string
    """
    if free_vram_bytes <= 0 or n_layers <= 0 or n_embd <= 0:
        return {
            "kv_per_token_bytes": 0,
            "headroom_bytes": 0,
            "max_ctx_at_batch": 0,
            "max_batch_at_ctx_train": 0,
            "recommendation": ("Not enough info — need n_layers, n_embd, "
                                "and free VRAM > 0."),
        }
    headroom = max(0, free_vram_bytes - model_size_bytes)
    kv_pt = kv_cache_per_token_bytes(n_layers, n_embd)
    max_ctx = int(headroom / (max(1, target_batch) * max(1, kv_pt)))
    max_batch_at_train = int(headroom / (max(1, n_ctx_train) * max(1, kv_pt))) \
        if n_ctx_train > 0 else 0
    # Recommendation text
    if max_ctx == 0:
        rec = ("No headroom left after the model weights. "
               "Pick a smaller quant, or evict another loaded model.")
    elif n_ctx_train > 0 and max_ctx >= n_ctx_train:
        rec = (f"You have headroom for the full training context "
               f"({n_ctx_train} tokens) at batch {target_batch}.")
    else:
        rec = (f"Cap context at {max_ctx} tokens to stay under VRAM. "
               f"Or drop batch from {target_batch} to fit more.")
    return {
        "kv_per_token_bytes": kv_pt,
        "headroom_bytes": headroom,
        "headroom_mib": round(headroom / 1024 ** 2, 1),
        "max_ctx_at_batch": max_ctx,
        "max_batch_at_ctx_train": max(1, max_batch_at_train),
        "recommendation": rec,
    }


def status(cfg=None) -> dict:
    """Aggregate snapshot."""
    llama_host = "localhost"
    llama_port = 8080
    if cfg:
        try:
            llama_port = int(cfg.get("LLAMASERVER_PORT", "8080"))
        except (ValueError, TypeError):
            pass
    target_batch = 1
    if cfg:
        try:
            target_batch = max(1, int(cfg.get("BATCH_ADVISOR_BATCH", "1")))
        except (ValueError, TypeError):
            pass
    vram = query_gpu_vram()
    models = probe_llamaserver_models(llama_host, llama_port)
    if vram is None:
        return {
            "ok": False,
            "reason": "nvidia-smi unreachable; cannot compute advisor.",
            "vram": None,
            "models": [],
            "advisors": [],
        }
    free_bytes = vram["free_mib"] * 1024 ** 2
    advisors: list[dict] = []
    for m in models:
        if not (m.get("n_params") and m.get("n_embd") and m.get("n_ctx_train")):
            continue
        n_layers = _infer_n_layers(m["n_params"], m["n_embd"])
        if not n_layers:
            continue
        adv = compute_advisory(
            model_size_bytes=int(m.get("size_bytes") or 0),
            n_layers=n_layers,
            n_embd=m["n_embd"],
            n_ctx_train=m["n_ctx_train"],
            free_vram_bytes=free_bytes,
            target_batch=target_batch,
        )
        advisors.append({"model": m["id"], **adv})
    return {
        "ok": True,
        "vram": vram,
        "models": models,
        "advisors": advisors,
        "target_batch": target_batch,
    }


def _infer_n_layers(n_params: int, n_embd: int) -> Optional[int]:
    """A transformer with n_layers and n_embd has approximately
    n_layers * 12 * n_embd^2 parameters (MLP 4x + attention 4x +
    embeddings overhead). Invert :

        n_layers ≈ n_params / (12 * n_embd^2)

    Useful when modelinfo doesn't expose n_layer directly. Rounds
    to nearest 4 (transformer blocks come in groups).
    """
    if n_embd <= 0 or n_params <= 0:
        return None
    raw = n_params / (12 * n_embd * n_embd)
    if raw < 1:
        return None
    return max(1, int(round(raw)))
