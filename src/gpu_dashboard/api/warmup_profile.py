"""HTTP handlers for /api/warmup-profile (R&D #19.4)."""
from __future__ import annotations

from typing import Optional, Tuple


Response = Tuple[int, dict]


def handle_warmup_profile_status(ctx: dict) -> Response:
    from ..modules import warmup_profile
    return 200, warmup_profile.status(ctx.get("config"))


def handle_warmup_profile_probe(ctx: dict, payload: dict) -> Response:
    """POST /api/warmup-profile/probe with body :
       {model: str, source: 'llamacpp' | 'ollama',
        host: str, port: int, prompt: str?}
    Sends one tiny inference and records the TTFT sample."""
    from ..modules import warmup_profile
    if not isinstance(payload, dict):
        return 400, {"ok": False, "error": "payload must be a dict"}
    model = str(payload.get("model", "")).strip()
    source = str(payload.get("source", "")).strip()
    host = str(payload.get("host", "localhost"))
    try:
        port = int(payload.get("port", 8080 if source == "llamacpp" else 11434))
    except (ValueError, TypeError):
        return 400, {"ok": False, "error": "port must be an integer"}
    prompt = str(payload.get("prompt", "Hi"))
    if source == "llamacpp":
        ttft = warmup_profile.probe_llamaserver(host, port, prompt)
    elif source == "ollama":
        if not model:
            return 400, {"ok": False, "error": "model required for ollama"}
        ttft = warmup_profile.probe_ollama(host, port, model, prompt)
    else:
        return 400, {"ok": False, "error": "source must be llamacpp or ollama"}
    if ttft is None:
        return 502, {"ok": False, "error": "probe failed"}
    warmup_profile.record_sample(model or "(default)", source,
                                  ttft, trigger="manual_probe")
    return 200, {"ok": True, "ttft_ms": round(ttft, 2)}
