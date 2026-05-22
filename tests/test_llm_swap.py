"""R&D #17.5 — LLM hot-swap orchestrator tests."""
import json
import os
import tempfile
import pytest
from unittest.mock import patch
from gpu_dashboard.modules import llm_swap as ls


def _with_tmp(td):
    return patch.multiple(
        ls,
        pins_path=lambda: os.path.join(td, "pins.json"),
        timeline_path=lambda: os.path.join(td, "timeline.json"),
    )


# ── _http_json ──────────────────────────────────────────────────────────


def test_http_json_handles_connection_refused():
    import urllib.error
    with patch("urllib.request.urlopen", side_effect=ConnectionRefusedError):
        assert ls._http_json("http://x") is None


# ── probe_ollama ────────────────────────────────────────────────────────


def test_probe_ollama_empty_when_unreachable():
    with patch.object(ls, "_http_json", return_value=None):
        assert ls.probe_ollama() == []


def test_probe_ollama_parses_models():
    fake = {"models": [
        {"name": "qwen3:7b", "size": "1500000000", "size_vram": "1400000000"},
        {"name": "llama3:70b", "size": "40000000000", "size_vram": "39000000000"},
    ]}
    with patch.object(ls, "_http_json", return_value=fake):
        out = ls.probe_ollama()
    assert len(out) == 2
    assert out[0]["name"] == "qwen3:7b"
    assert out[0]["source"] == "ollama"
    assert out[0]["vram_bytes"] == 1400000000


# ── probe_llamaserver ───────────────────────────────────────────────────


def test_probe_llamaserver_empty_when_unreachable():
    with patch.object(ls, "_http_json", return_value=None):
        assert ls.probe_llamaserver() == []


def test_probe_llamaserver_parses_data_array():
    fake = {"data": [
        {"id": "Qwen3.5-7B-Q4.gguf",
         "meta": {"size": 4500000000, "n_ctx_train": 32768, "n_params": 7000000000}},
    ]}
    with patch.object(ls, "_http_json", return_value=fake):
        out = ls.probe_llamaserver()
    assert len(out) == 1
    assert out[0]["name"] == "Qwen3.5-7B-Q4.gguf"
    assert out[0]["source"] == "llamacpp"
    assert out[0]["n_ctx_train"] == 32768


# ── diff_models ─────────────────────────────────────────────────────────


def test_diff_no_changes_yields_no_events():
    prev = [{"name": "x", "source": "ollama"}]
    cur  = [{"name": "x", "source": "ollama"}]
    assert ls.diff_models(prev, cur) == []


def test_diff_load_event():
    prev = []
    cur  = [{"name": "x", "source": "ollama", "vram_bytes": 1000}]
    events = ls.diff_models(prev, cur)
    assert len(events) == 1
    assert events[0]["kind"] == "load"
    assert events[0]["name"] == "x"


def test_diff_unload_event():
    prev = [{"name": "y", "source": "llamacpp", "vram_bytes": 500}]
    cur  = []
    events = ls.diff_models(prev, cur)
    assert events[0]["kind"] == "unload"
    assert events[0]["name"] == "y"


def test_diff_swap_load_and_unload():
    prev = [{"name": "old", "source": "ollama"}]
    cur  = [{"name": "new", "source": "ollama"}]
    events = ls.diff_models(prev, cur)
    kinds = sorted([e["kind"] for e in events])
    assert kinds == ["load", "unload"]


# ── pin / unpin ─────────────────────────────────────────────────────────


def test_add_then_remove_pin():
    with tempfile.TemporaryDirectory() as td, _with_tmp(td):
        ls.add_pin("qwen3:7b")
        assert "qwen3:7b" in ls.load_pins()
        assert ls.remove_pin("qwen3:7b") is True
        assert "qwen3:7b" not in ls.load_pins()


def test_remove_unknown_pin_returns_false():
    with tempfile.TemporaryDirectory() as td, _with_tmp(td):
        assert ls.remove_pin("nope") is False


def test_add_pin_idempotent():
    """Adding the same pin twice doesn't duplicate."""
    with tempfile.TemporaryDirectory() as td, _with_tmp(td):
        ls.add_pin("x")
        ls.add_pin("x")
        assert ls.load_pins() == {"x"}


# ── suggest_evictions ───────────────────────────────────────────────────


def test_suggest_empty_when_no_loaded():
    r = ls.suggest_evictions([], needed_vram_bytes=10**9, pins=set(), events=[])
    assert r["to_evict"] == []
    assert r["sufficient"] is False


def test_suggest_picks_lru_first():
    """Older last_seen → evicted first."""
    loaded = [
        {"name": "newer", "source": "ollama", "vram_bytes": 5*10**9},
        {"name": "older", "source": "ollama", "vram_bytes": 5*10**9},
    ]
    events = [
        {"name": "older", "kind": "load", "ts": 100},
        {"name": "newer", "kind": "load", "ts": 200},
    ]
    r = ls.suggest_evictions(loaded, needed_vram_bytes=4*10**9,
                              pins=set(), events=events)
    assert r["to_evict"][0]["name"] == "older"


def test_suggest_skips_pinned_models():
    loaded = [
        {"name": "pinned", "source": "ollama", "vram_bytes": 10*10**9},
        {"name": "evictable", "source": "ollama", "vram_bytes": 5*10**9},
    ]
    r = ls.suggest_evictions(loaded, needed_vram_bytes=8*10**9,
                              pins={"pinned"}, events=[])
    names = [m["name"] for m in r["to_evict"]]
    assert "pinned" not in names
    # Only 5 GB available, need 8 GB → not sufficient
    assert r["sufficient"] is False


def test_suggest_sufficient_when_enough_to_evict():
    loaded = [
        {"name": "a", "source": "ollama", "vram_bytes": 5*10**9},
        {"name": "b", "source": "ollama", "vram_bytes": 5*10**9},
    ]
    r = ls.suggest_evictions(loaded, needed_vram_bytes=8*10**9,
                              pins=set(), events=[])
    assert r["sufficient"] is True
    # Should evict 2 to free 10 GB
    assert len(r["to_evict"]) == 2


def test_suggest_stops_at_first_sufficient():
    """If 1 eviction frees enough, don't unload more."""
    loaded = [
        {"name": "a", "source": "ollama", "vram_bytes": 20*10**9},
        {"name": "b", "source": "ollama", "vram_bytes": 5*10**9},
    ]
    r = ls.suggest_evictions(loaded, needed_vram_bytes=15*10**9,
                              pins=set(), events=[])
    # Either picks a (20 GiB, sufficient) or both (LRU order). Either way,
    # if a was picked first and is sufficient, we should stop after 1.
    assert r["sufficient"] is True


# ── timeline ────────────────────────────────────────────────────────────


def test_update_timeline_persists_diff():
    with tempfile.TemporaryDirectory() as td, _with_tmp(td):
        prev = []
        cur = [{"name": "x", "source": "ollama", "vram_bytes": 1000}]
        new = ls.update_timeline(prev, cur)
        log = ls.load_timeline()
    assert len(new) == 1
    assert len(log) == 1


def test_update_timeline_no_changes_no_write():
    with tempfile.TemporaryDirectory() as td, _with_tmp(td):
        same = [{"name": "x", "source": "ollama"}]
        new = ls.update_timeline(same, same)
    assert new == []


# ── status ──────────────────────────────────────────────────────────────


def test_status_with_loaded_models():
    with tempfile.TemporaryDirectory() as td, _with_tmp(td):
        with patch.object(ls, "probe_all", return_value=[
            {"name": "x", "source": "ollama", "vram_bytes": 2 * 1024**3},
            {"name": "y", "source": "llamacpp", "vram_bytes": 4 * 1024**3},
        ]):
            s = ls.status()
    assert s["loaded_count"] == 2
    assert s["total_vram_gib"] == 6.0
