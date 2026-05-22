"""Module warmup_profile — Per-model warm-up profiler (R&D #19.4).

When you swap LLMs in and out of VRAM (see #17.5), the first inference
after a model is loaded is dramatically slower than the next — page
faults, CUDA kernel JIT, weight-tensor pre-pinning. Users have no
data to justify "pinning" a model vs evicting it.

This module observes the llm_swap timeline events (load / unload) and
correlates them with optional active first-token probes against
llama-server / Ollama to record cold→hot first-token-latency curves
per model.

Stores rolling samples per model name in
~/.config/gpu-dashboard/warmup_profile.json. Capped at 50 samples
per model.

stdlib only : urllib + json.
"""
from __future__ import annotations

import json
import os
import threading
import time
import urllib.error
import urllib.request
from typing import Optional


NAME = "warmup_profile"


_PROFILE_PATH = "~/.config/gpu-dashboard/warmup_profile.json"
_PROFILE_MAX_SAMPLES = 50

_lock = threading.Lock()


def profile_path() -> str:
    return os.path.expanduser(_PROFILE_PATH)


def load_profile() -> dict:
    p = profile_path()
    if not os.path.exists(p):
        return {}
    try:
        with open(p) as f:
            d = json.load(f)
        return d if isinstance(d, dict) else {}
    except (OSError, json.JSONDecodeError):
        return {}


def save_profile(profile: dict) -> None:
    p = profile_path()
    os.makedirs(os.path.dirname(p), exist_ok=True)
    with open(p, "w") as f:
        json.dump(profile, f, indent=2)


def record_sample(model: str, source: str, ttft_ms: float,
                   trigger: str = "manual") -> None:
    """Append one (timestamp, ttft_ms) sample for a model and persist."""
    with _lock:
        prof = load_profile()
        entry = prof.get(model, {"source": source, "samples": []})
        entry["source"] = source
        entry["samples"].append({
            "ts": int(time.time()),
            "ttft_ms": round(ttft_ms, 2),
            "trigger": trigger,
        })
        entry["samples"] = entry["samples"][-_PROFILE_MAX_SAMPLES:]
        prof[model] = entry
        save_profile(prof)


def probe_llamaserver(host: str = "localhost", port: int = 8080,
                       prompt: str = "Hi",
                       timeout: float = 30.0) -> Optional[float]:
    """Send a single-token completion request to llama-server's OpenAI
    endpoint and return the time to first byte in ms. None on failure."""
    url = f"http://{host}:{port}/v1/chat/completions"
    body = json.dumps({
        "messages": [{"role": "user", "content": prompt}],
        "max_tokens": 1,
        "stream": False,
    }).encode()
    req = urllib.request.Request(
        url, data=body, headers={"Content-Type": "application/json"})
    t0 = time.perf_counter()
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            r.read(1)
    except (urllib.error.URLError, urllib.error.HTTPError, OSError,
            TimeoutError):
        return None
    return (time.perf_counter() - t0) * 1000


def probe_ollama(host: str = "localhost", port: int = 11434,
                  model: str = "",
                  prompt: str = "Hi",
                  timeout: float = 60.0) -> Optional[float]:
    """Send a tiny /api/generate request to Ollama and return TTFT ms.
    None on failure."""
    if not model:
        return None
    url = f"http://{host}:{port}/api/generate"
    body = json.dumps({
        "model": model, "prompt": prompt,
        "stream": False, "options": {"num_predict": 1},
    }).encode()
    req = urllib.request.Request(
        url, data=body, headers={"Content-Type": "application/json"})
    t0 = time.perf_counter()
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            r.read(1)
    except (urllib.error.URLError, urllib.error.HTTPError, OSError,
            TimeoutError):
        return None
    return (time.perf_counter() - t0) * 1000


def summarize(samples: list) -> dict:
    """Compute aggregate stats : count, ttft_min, ttft_max, ttft_median,
    cold_minus_hot_ms (1st sample minus median of next N)."""
    if not samples:
        return {"count": 0}
    ttfts = sorted(s["ttft_ms"] for s in samples)
    n = len(ttfts)
    median = ttfts[n // 2] if n % 2 else (ttfts[n // 2 - 1] + ttfts[n // 2]) / 2
    cold = samples[0]["ttft_ms"]
    hot_pool = sorted(s["ttft_ms"] for s in samples[1:])
    hot_median = (hot_pool[len(hot_pool) // 2]
                  if hot_pool and len(hot_pool) % 2
                  else ((hot_pool[len(hot_pool) // 2 - 1]
                          + hot_pool[len(hot_pool) // 2]) / 2
                         if hot_pool else None))
    return {
        "count": n,
        "ttft_min": min(ttfts),
        "ttft_max": max(ttfts),
        "ttft_median": median,
        "cold_ttft_ms": cold,
        "hot_median_ttft_ms": hot_median,
        "cold_minus_hot_ms": (cold - hot_median) if hot_median else None,
    }


def recommendation_for(model: str, stats: dict) -> str:
    """One-liner verdict per model."""
    count = stats.get("count", 0)
    if count == 0:
        return "no samples yet — first request will populate the profile."
    cold_delta = stats.get("cold_minus_hot_ms")
    if cold_delta is None or count < 3:
        return f"{count} sample(s) — need at least 3 for a stable verdict."
    if cold_delta > 1000:
        return (f"Cold start is {cold_delta:.0f} ms slower than hot. "
                f"Consider pinning '{model}' if you query it frequently.")
    return (f"Warm/cold gap is only {cold_delta:.0f} ms — pinning would not "
            "save much.")


def status(cfg=None) -> dict:
    """Aggregate snapshot for the UI."""
    prof = load_profile()
    out: list = []
    for model, entry in prof.items():
        samples = entry.get("samples", [])
        stats = summarize(samples)
        out.append({
            "model": model,
            "source": entry.get("source", "?"),
            "samples": samples,
            "stats": stats,
            "recommendation": recommendation_for(model, stats),
        })
    return {
        "ok": True,
        "models": out,
        "tracked_count": len(out),
    }
