"""Module llm_swap — LLM hot-swap orchestrator (R&D #17.5).

Single-GPU rigs running Ollama or llama-server can only hold so many
models in VRAM. When you want to switch from Qwen3-7B to Llama-3-70B
you usually have to manually `ollama stop` first. This module :

  1. Probes Ollama /api/ps + llama-server /v1/models every poll
  2. Maintains an LRU table of when each model was last seen 'loaded'
  3. Exposes pin/unpin so 'always-on' models survive eviction
  4. Records swap events (load/unload) on a timeline
  5. Estimates VRAM headroom before suggesting an eviction

Out of scope (could be a follow-up) : actually CALLING ollama's unload
endpoint to trigger eviction. We expose the candidate list ; the user
decides.

stdlib only : urllib + json + threading lock for the swap log.
"""
from __future__ import annotations

import json
import os
import threading
import time
import urllib.error
import urllib.request
from typing import Optional


NAME = "llm_swap"

_PINS_PATH = "~/.config/gpu-dashboard/llm_pins.json"
_TIMELINE_PATH = "~/.config/gpu-dashboard/llm_swap_timeline.json"
_TIMELINE_MAX = 200

_lock = threading.Lock()


def pins_path() -> str:
    return os.path.expanduser(_PINS_PATH)


def timeline_path() -> str:
    return os.path.expanduser(_TIMELINE_PATH)


def load_pins() -> set:
    p = pins_path()
    if not os.path.exists(p):
        return set()
    try:
        with open(p) as f:
            d = json.load(f)
        return set(d) if isinstance(d, list) else set()
    except (OSError, json.JSONDecodeError):
        return set()


def save_pins(pins: set) -> None:
    p = pins_path()
    os.makedirs(os.path.dirname(p), exist_ok=True)
    with open(p, "w") as f:
        json.dump(sorted(pins), f, indent=2)


def load_timeline() -> list:
    p = timeline_path()
    if not os.path.exists(p):
        return []
    try:
        with open(p) as f:
            d = json.load(f)
        return d if isinstance(d, list) else []
    except (OSError, json.JSONDecodeError):
        return []


def save_timeline(events: list) -> None:
    p = timeline_path()
    os.makedirs(os.path.dirname(p), exist_ok=True)
    events = events[-_TIMELINE_MAX:]
    with open(p, "w") as f:
        json.dump(events, f, indent=2)


def _http_json(url: str, timeout: float = 1.5) -> Optional[dict]:
    try:
        with urllib.request.urlopen(url, timeout=timeout) as r:
            if r.status != 200:
                return None
            return json.loads(r.read().decode("utf-8"))
    except (urllib.error.URLError, urllib.error.HTTPError, OSError,
            json.JSONDecodeError, TimeoutError):
        return None


def probe_ollama(host: str = "localhost", port: int = 11434) -> list:
    """Return list of {name, source: 'ollama', size_bytes, vram_bytes, expires_at}.
    Empty list if Ollama not running."""
    d = _http_json(f"http://{host}:{port}/api/ps")
    if not d:
        return []
    models = d.get("models", []) if isinstance(d, dict) else []
    out: list = []
    for m in models:
        if not isinstance(m, dict):
            continue
        out.append({
            "name": m.get("name") or m.get("model") or "?",
            "source": "ollama",
            "size_bytes": int(m.get("size") or 0) if str(m.get("size") or "").isdigit() else 0,
            "vram_bytes": int(m.get("size_vram") or 0) if str(m.get("size_vram") or "").isdigit() else 0,
            "expires_at": m.get("expires_at", ""),
        })
    return out


def probe_llamaserver(host: str = "localhost", port: int = 8080) -> list:
    """Return list of {name, source: 'llamacpp', size_bytes, n_ctx_train, ...}.
    Empty if llama-server not reachable."""
    d = _http_json(f"http://{host}:{port}/v1/models")
    if not d:
        return []
    items = d.get("data") or d.get("models") or []
    out: list = []
    for m in items:
        if not isinstance(m, dict):
            continue
        meta = m.get("meta", {}) if isinstance(m.get("meta"), dict) else {}
        out.append({
            "name": m.get("id") or m.get("name") or "?",
            "source": "llamacpp",
            "size_bytes": int(meta.get("size") or 0),
            "n_ctx_train": meta.get("n_ctx_train"),
            "n_params": meta.get("n_params"),
        })
    return out


def probe_all(cfg=None) -> list:
    """Combined probe from configured Ollama + llama-server endpoints."""
    ollama_host = "localhost"
    ollama_port = 11434
    llama_host = "localhost"
    llama_port = 8080
    if cfg:
        try:
            ollama_port = int(cfg.get("OLLAMA_PORT", "11434"))
        except (ValueError, TypeError):
            pass
        try:
            llama_port = int(cfg.get("LLAMASERVER_PORT", "8080"))
        except (ValueError, TypeError):
            pass
    return probe_ollama(ollama_host, ollama_port) + probe_llamaserver(llama_host, llama_port)


def _model_lru_key(events: list) -> dict:
    """Walk timeline events backwards to produce {model_name: last_ts}."""
    last_seen: dict = {}
    for e in reversed(events):
        name = e.get("name")
        if name and name not in last_seen:
            last_seen[name] = e.get("ts", 0)
    return last_seen


def diff_models(prev: list, current: list) -> list:
    """Detect load + unload events between two probes. Returns list of
    {kind: load|unload, name, source, ts}."""
    prev_names = {f"{m['source']}:{m['name']}": m for m in prev}
    cur_names = {f"{m['source']}:{m['name']}": m for m in current}
    events: list = []
    now = int(time.time())
    for key, m in cur_names.items():
        if key not in prev_names:
            events.append({"kind": "load", "name": m["name"],
                           "source": m["source"], "ts": now,
                           "vram_bytes": m.get("vram_bytes", 0)})
    for key, m in prev_names.items():
        if key not in cur_names:
            events.append({"kind": "unload", "name": m["name"],
                           "source": m["source"], "ts": now})
    return events


def update_timeline(prev: list, current: list) -> list:
    """Compute diff + append to timeline + persist. Returns the new events
    that were added."""
    events = diff_models(prev, current)
    if not events:
        return []
    with _lock:
        log = load_timeline()
        log.extend(events)
        save_timeline(log)
    return events


def suggest_evictions(loaded: list, needed_vram_bytes: int,
                       pins: Optional[set] = None,
                       events: Optional[list] = None) -> dict:
    """Given a target needed_vram_bytes, suggest which loaded models to
    unload (LRU + size-aware). Pinned models are never suggested.
    Returns {to_evict: [...], freed_bytes, sufficient: bool, reason}."""
    if pins is None:
        pins = load_pins()
    if events is None:
        events = load_timeline()
    last_seen = _model_lru_key(events)
    candidates = [m for m in loaded if m.get("name") not in pins
                  and m.get("vram_bytes", 0) > 0]
    # Sort by LRU (oldest first) then by largest size
    candidates.sort(key=lambda m: (last_seen.get(m["name"], 0), -m.get("vram_bytes", 0)))
    chosen: list = []
    freed = 0
    for m in candidates:
        if freed >= needed_vram_bytes:
            break
        chosen.append({"name": m["name"], "source": m["source"],
                       "vram_bytes": m.get("vram_bytes", 0),
                       "last_seen": last_seen.get(m["name"], 0)})
        freed += m.get("vram_bytes", 0)
    sufficient = freed >= needed_vram_bytes
    return {
        "to_evict": chosen,
        "freed_bytes": freed,
        "sufficient": sufficient,
        "needed_bytes": needed_vram_bytes,
        "reason": "sufficient" if sufficient else "cannot free enough (pins block more)",
    }


def add_pin(name: str) -> None:
    pins = load_pins()
    pins.add(name)
    save_pins(pins)


def remove_pin(name: str) -> bool:
    pins = load_pins()
    if name in pins:
        pins.discard(name)
        save_pins(pins)
        return True
    return False


def status(cfg=None) -> dict:
    """Top-level snapshot for the UI."""
    loaded = probe_all(cfg)
    pins = load_pins()
    timeline = load_timeline()
    total_vram = sum(m.get("vram_bytes", 0) for m in loaded)
    return {
        "ok": True,
        "loaded_count": len(loaded),
        "loaded": loaded,
        "total_vram_bytes": total_vram,
        "total_vram_gib": round(total_vram / 1024**3, 2),
        "pins": sorted(pins),
        "timeline_count": len(timeline),
        "recent_events": timeline[-30:],
    }
