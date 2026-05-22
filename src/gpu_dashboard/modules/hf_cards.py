"""Module hf_cards — Hugging Face model card cross-reference (R&D #10.3).

When the dashboard detects a known model loaded in VRAM (via process
cmdline / GGUF path), fetch its model card metadata from HF Hub and
annotate the running-model card with license, base_model, downloads,
likes — turning the dashboard into a 'what am I actually running' ref.

stdlib only. Cache responses in JSON file with 7-day TTL.
Offline-first : stale cache OK ; fail silent on network errors.
"""
from __future__ import annotations

import json
import os
import re
import time
import urllib.error
import urllib.request
from typing import Optional


NAME = "hf_cards"

_API_BASE = "https://huggingface.co/api/models/"
_TIMEOUT = 4.0
_CACHE_TTL_S = 7 * 86400  # 7 days
_CACHE_PATH = "~/.config/gpu-dashboard/hf_cards.json"


def cache_path() -> str:
    return os.path.expanduser(_CACHE_PATH)


def load_cache() -> dict:
    path = cache_path()
    if not os.path.exists(path):
        return {}
    try:
        with open(path) as f:
            d = json.load(f)
        return d if isinstance(d, dict) else {}
    except (OSError, json.JSONDecodeError):
        return {}


def save_cache(cache: dict) -> None:
    path = cache_path()
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w") as f:
        json.dump(cache, f, indent=2)


def parse_repo_from_path(path: str) -> Optional[str]:
    """Extract HF repo id from a model file path or arg.

    Examples :
      ~/.cache/huggingface/hub/models--Qwen--Qwen2.5-7B-Instruct-GGUF/blobs/...
        → Qwen/Qwen2.5-7B-Instruct-GGUF
      /home/x/models/Qwen3.5-27B-Q4_K_M.gguf
        → Qwen/Qwen3.5-27B-Q4_K_M (heuristic guess)
      Qwen/Qwen2.5-7B
        → Qwen/Qwen2.5-7B (already a repo id)
    """
    if not path:
        return None
    # Direct repo-id form (org/repo)
    if "/" in path and not path.startswith("/") and not path.startswith("~"):
        # crude check : at most 1 slash, no spaces, alnum/-
        bare = path.strip()
        if bare.count("/") == 1 and re.match(r"^[\w.\-]+/[\w.\-]+$", bare):
            return bare

    # HF cache layout : models--ORG--REPO
    m = re.search(r"models--([\w.\-]+)--([\w.\-]+?)(?:/|$)", path)
    if m:
        return f"{m.group(1)}/{m.group(2)}"

    # Bare filename heuristic — too risky to guess org. Return None.
    return None


def fetch_card(repo_id: str) -> Optional[dict]:
    """Fetch model card metadata from HF API. Returns the normalized dict
    or None on network error."""
    url = _API_BASE + repo_id
    try:
        req = urllib.request.Request(
            url, headers={"User-Agent": "gpu-dashboard/0.3 (+https://github.com/Shad107/gpu-dashboard)"},
        )
        with urllib.request.urlopen(req, timeout=_TIMEOUT) as r:
            if r.status != 200:
                return None
            data = json.loads(r.read().decode("utf-8"))
    except (urllib.error.URLError, urllib.error.HTTPError, OSError,
            json.JSONDecodeError, TimeoutError):
        return None
    return normalize(data)


def normalize(raw: dict) -> dict:
    """Extract the bits we care about from a HF API response."""
    card = raw.get("cardData") if isinstance(raw.get("cardData"), dict) else {}
    return {
        "id": raw.get("modelId") or raw.get("id"),
        "author": raw.get("author"),
        "downloads": raw.get("downloads"),
        "likes": raw.get("likes"),
        "license": card.get("license"),
        "license_link": card.get("license_link"),
        "base_model": _stringify(card.get("base_model")),
        "pipeline_tag": raw.get("pipeline_tag") or card.get("pipeline_tag"),
        "tags": raw.get("tags") or [],
        "last_modified": raw.get("lastModified"),
        "fetched_ts": int(time.time()),
    }


def _stringify(v) -> Optional[str]:
    """base_model can be string or list[str]. Normalize to first string."""
    if v is None:
        return None
    if isinstance(v, list):
        return v[0] if v else None
    return str(v)


def get_card(repo_id: str, force_refresh: bool = False) -> Optional[dict]:
    """Return cached + fetched-if-stale model card for a repo id.

    Offline-first :
      1. If cache fresh (< 7d), return it
      2. Try to fetch ; if success, update cache
      3. If fetch fails, return stale cache (better than nothing)
      4. If no cache and no fetch, return None
    """
    cache = load_cache()
    entry = cache.get(repo_id)
    now = int(time.time())
    fresh = (entry is not None and isinstance(entry, dict)
             and (now - int(entry.get("fetched_ts", 0))) < _CACHE_TTL_S)
    if entry and fresh and not force_refresh:
        return entry
    fresh_data = fetch_card(repo_id)
    if fresh_data:
        cache[repo_id] = fresh_data
        save_cache(cache)
        return fresh_data
    return entry  # stale or None


def license_color(lic: Optional[str]) -> str:
    """Return a shields.io-compatible color for a license name."""
    if not lic:
        return "#9f9f9f"
    s = lic.lower()
    # Permissive
    if any(k in s for k in ("mit", "apache", "bsd", "isc", "unlicense")):
        return "#4c1"  # green
    # Copyleft
    if any(k in s for k in ("gpl", "lgpl", "agpl", "mozilla")):
        return "#dfb317"  # yellow
    # Restrictive / custom / non-commercial
    if any(k in s for k in ("non-commercial", "research", "cc-by-nc",
                            "llama", "openrail", "stable")):
        return "#e05d44"  # red
    return "#007ec6"  # neutral blue
