"""R&D #19.4 — Per-model warm-up profiler tests."""
import os
import tempfile
import pytest
from unittest.mock import patch
from gpu_dashboard.modules import warmup_profile as wp


def _with_tmp(td):
    return patch.object(wp, "profile_path",
                        lambda: os.path.join(td, "warmup.json"))


# ── load / save profile ────────────────────────────────────────────────


def test_load_empty_when_missing(tmp_path):
    with _with_tmp(str(tmp_path)):
        assert wp.load_profile() == {}


def test_save_and_reload_roundtrip(tmp_path):
    with _with_tmp(str(tmp_path)):
        wp.save_profile({"foo": {"source": "ollama", "samples": []}})
        loaded = wp.load_profile()
    assert "foo" in loaded


def test_load_handles_malformed_json(tmp_path):
    with _with_tmp(str(tmp_path)):
        with open(wp.profile_path(), "w") as f:
            f.write("{not json")
        assert wp.load_profile() == {}


# ── record_sample ──────────────────────────────────────────────────────


def test_record_first_sample(tmp_path):
    with _with_tmp(str(tmp_path)):
        wp.record_sample("Qwen3.5-35B", "llamacpp", 850.0)
        prof = wp.load_profile()
    assert "Qwen3.5-35B" in prof
    assert len(prof["Qwen3.5-35B"]["samples"]) == 1
    assert prof["Qwen3.5-35B"]["samples"][0]["ttft_ms"] == 850.0


def test_record_appends(tmp_path):
    with _with_tmp(str(tmp_path)):
        wp.record_sample("x", "ollama", 100.0)
        wp.record_sample("x", "ollama", 200.0)
        prof = wp.load_profile()
    assert len(prof["x"]["samples"]) == 2


def test_record_caps_at_max(tmp_path):
    with _with_tmp(str(tmp_path)):
        for i in range(wp._PROFILE_MAX_SAMPLES + 20):
            wp.record_sample("x", "ollama", float(i))
        prof = wp.load_profile()
    assert len(prof["x"]["samples"]) == wp._PROFILE_MAX_SAMPLES
    # Should retain newest samples
    last_value = prof["x"]["samples"][-1]["ttft_ms"]
    assert last_value == float(wp._PROFILE_MAX_SAMPLES + 19)


# ── summarize ──────────────────────────────────────────────────────────


def test_summarize_empty():
    assert wp.summarize([]) == {"count": 0}


def test_summarize_one_sample():
    s = wp.summarize([{"ts": 1, "ttft_ms": 500}])
    assert s["count"] == 1
    assert s["ttft_min"] == 500
    assert s["ttft_max"] == 500
    assert s["cold_ttft_ms"] == 500
    assert s["hot_median_ttft_ms"] is None  # no hot samples yet


def test_summarize_cold_then_hot():
    samples = [
        {"ts": 1, "ttft_ms": 2000},  # cold
        {"ts": 2, "ttft_ms": 200},
        {"ts": 3, "ttft_ms": 180},
        {"ts": 4, "ttft_ms": 220},
    ]
    s = wp.summarize(samples)
    assert s["count"] == 4
    assert s["cold_ttft_ms"] == 2000
    assert s["hot_median_ttft_ms"] == 200
    assert s["cold_minus_hot_ms"] == 1800


def test_summarize_min_max():
    samples = [
        {"ts": 1, "ttft_ms": 500},
        {"ts": 2, "ttft_ms": 100},
        {"ts": 3, "ttft_ms": 900},
    ]
    s = wp.summarize(samples)
    assert s["ttft_min"] == 100
    assert s["ttft_max"] == 900


# ── recommendation_for ─────────────────────────────────────────────────


def test_recommend_no_samples():
    r = wp.recommendation_for("x", {"count": 0})
    assert "no samples" in r


def test_recommend_too_few_samples():
    r = wp.recommendation_for("x", {"count": 2, "cold_minus_hot_ms": 500})
    assert "at least 3" in r


def test_recommend_pin_when_big_cold_gap():
    r = wp.recommendation_for("Qwen", {"count": 5, "cold_minus_hot_ms": 5000})
    assert "pinning" in r.lower() or "pin" in r.lower()


def test_recommend_skip_when_small_gap():
    r = wp.recommendation_for("Qwen", {"count": 5, "cold_minus_hot_ms": 50})
    assert "not save" in r


# ── probe_llamaserver / probe_ollama ───────────────────────────────────


def test_probe_llamaserver_failure():
    with patch("urllib.request.urlopen", side_effect=ConnectionRefusedError):
        assert wp.probe_llamaserver(timeout=0.1) is None


def test_probe_ollama_failure():
    with patch("urllib.request.urlopen", side_effect=ConnectionRefusedError):
        assert wp.probe_ollama(model="qwen3:7b", timeout=0.1) is None


def test_probe_ollama_requires_model_name():
    assert wp.probe_ollama(model="", timeout=0.1) is None


# ── status ─────────────────────────────────────────────────────────────


def test_status_empty(tmp_path):
    with _with_tmp(str(tmp_path)):
        s = wp.status()
    assert s["tracked_count"] == 0
    assert s["models"] == []


def test_status_with_recorded_model(tmp_path):
    with _with_tmp(str(tmp_path)):
        wp.record_sample("Qwen3.5-35B", "llamacpp", 2000.0)
        wp.record_sample("Qwen3.5-35B", "llamacpp", 200.0)
        wp.record_sample("Qwen3.5-35B", "llamacpp", 180.0)
        wp.record_sample("Qwen3.5-35B", "llamacpp", 220.0)
        s = wp.status()
    assert s["tracked_count"] == 1
    m = s["models"][0]
    assert m["model"] == "Qwen3.5-35B"
    assert m["stats"]["count"] == 4
    assert "pin" in m["recommendation"].lower()
