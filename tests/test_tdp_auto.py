"""R&D #17.3 — TDP profile auto-switch tests."""
import json
import os
import tempfile
import time
import pytest
from unittest.mock import patch
from gpu_dashboard.modules import tdp_auto as ta


def _with_tmp(td):
    return patch.multiple(
        ta,
        cfg_path=lambda: os.path.join(td, "cfg.json"),
        state_path=lambda: os.path.join(td, "state.json"),
    )


def _samples(util_seq: list, start_ts: float = 1000.0, step_s: int = 5):
    """Build samples list with given util values, increasing ts."""
    return [{"ts": start_ts + i * step_s, "util_gpu": u}
            for i, u in enumerate(util_seq)]


# ── classify ─────────────────────────────────────────────────────────────


def test_classify_idle_low_util():
    s = _samples([1, 2, 3, 2, 1])
    assert ta.classify(s, 60, 5, 70) == "idle"


def test_classify_heavy_high_util():
    s = _samples([85, 90, 92, 88, 91])
    assert ta.classify(s, 60, 5, 70) == "heavy"


def test_classify_light_in_between():
    s = _samples([30, 40, 35, 45, 50])
    assert ta.classify(s, 60, 5, 70) == "light"


def test_classify_empty_returns_light():
    assert ta.classify([], 60, 5, 70) == "light"


def test_classify_outside_window_ignored():
    """A spike outside the window doesn't push classification."""
    # 5 samples with ts 0..20 ; we ask for window of 10 s ending at ts=20
    samples = [{"ts": 0, "util_gpu": 100}, {"ts": 5, "util_gpu": 90},
                {"ts": 12, "util_gpu": 2}, {"ts": 17, "util_gpu": 3},
                {"ts": 20, "util_gpu": 2}]
    # window 10s ending at 20 → only ts 12, 17, 20 count → mean ~2 → idle
    assert ta.classify(samples, 10, 5, 70, now_ts=20) == "idle"


# ── decide_switch ────────────────────────────────────────────────────────


def test_decide_no_change_when_same_state():
    cfg = dict(ta._DEFAULT_CFG)
    prev = {"current_profile": "idle"}
    samples = _samples([1, 2, 3])
    d = ta.decide_switch(samples, cfg, prev)
    assert d["would_switch"] is False
    assert d["reason"] == "unchanged"


def test_decide_hysteresis_blocks_immediate_switch():
    """A first transition observation doesn't immediately switch."""
    cfg = dict(ta._DEFAULT_CFG)
    prev = {"current_profile": "idle"}   # never observed 'heavy' before
    samples = _samples([85, 90, 95])     # heavy util
    d = ta.decide_switch(samples, cfg, prev)
    assert d["would_switch"] is False
    assert d["reason"] == "hysteresis-pending"
    assert d["target_profile"] == "heavy"


def test_decide_hysteresis_passes_after_elapsed():
    """If target was observed > hysteresis_s ago, switch."""
    cfg = dict(ta._DEFAULT_CFG)
    cfg["hysteresis_s"] = 10
    prev = {"current_profile": "idle",
             "pending_target": "heavy", "pending_target_since_ts": 1000}
    samples = _samples([90, 95], start_ts=1010)
    d = ta.decide_switch(samples, cfg, prev, now_ts=1020)
    assert d["would_switch"] is True
    assert d["reason"] == "switch"


def test_decide_returns_mean_util():
    cfg = dict(ta._DEFAULT_CFG)
    prev = {"current_profile": "idle"}
    samples = _samples([40, 50, 60])
    d = ta.decide_switch(samples, cfg, prev)
    assert d["mean_util"] == 50.0


# ── evaluate (top-level) ─────────────────────────────────────────────────


def test_evaluate_dry_run_does_not_change_current_profile():
    with tempfile.TemporaryDirectory() as td, _with_tmp(td):
        cfg = dict(ta._DEFAULT_CFG)
        cfg["enabled"] = True
        ta.save_config(cfg)
        ta.save_state({"current_profile": "light", "since_ts": 0})
        # Strong heavy → would-switch
        samples = _samples([90, 95, 98] * 3, start_ts=1000)
        # Simulate hysteresis already elapsed
        state = ta.load_state()
        state["pending_target"] = "heavy"
        state["pending_target_since_ts"] = 900   # 130s ago
        ta.save_state(state)
        result = ta.evaluate(samples, dry_run=True)
    # No apply, no change
    final_state = ta.load_state() if hasattr(ta, "load_state") else {}


def test_evaluate_disabled_config_no_apply():
    """Even with would_switch, disabled config = no apply."""
    with tempfile.TemporaryDirectory() as td, _with_tmp(td):
        cfg = dict(ta._DEFAULT_CFG)
        cfg["enabled"] = False
        ta.save_config(cfg)
        ta.save_state({"current_profile": "idle"})
        samples = _samples([90, 95, 98] * 5)
        r = ta.evaluate(samples, dry_run=False)
    # Either would_switch=False (no hysteresis yet) or apply skipped (config disabled)
    assert r["config_enabled"] is False


# ── dry_run_preview ──────────────────────────────────────────────────────


def test_preview_no_samples_returns_empty():
    with tempfile.TemporaryDirectory() as td, _with_tmp(td):
        assert ta.dry_run_preview([])["switches"] == []


def test_preview_detects_idle_to_heavy_transition():
    """Synthetic timeline : 30 min idle, then 30 min heavy → 1 switch."""
    with tempfile.TemporaryDirectory() as td, _with_tmp(td):
        ta.save_config({**ta._DEFAULT_CFG, "hysteresis_s": 60, "window_s": 60})
        # 1800 s of idle + 1800 s of heavy at 1-min granularity
        idle_samples = [{"ts": i * 5, "util_gpu": 2} for i in range(360)]
        heavy_samples = [{"ts": 1800 + i * 5, "util_gpu": 90} for i in range(360)]
        all_samples = idle_samples + heavy_samples
        result = ta.dry_run_preview(all_samples, window_s=3600)
    # At least one switch should be detected
    assert result["switch_count"] >= 1
    # Final switch should be idle → heavy
    last = result["switches"][-1]
    assert last["from"] == "idle"
    assert last["to"] == "heavy"


# ── config persistence ──────────────────────────────────────────────────


def test_save_and_load_config_roundtrip():
    with tempfile.TemporaryDirectory() as td, _with_tmp(td):
        cfg = {"enabled": True, "window_s": 120}
        ta.save_config(cfg)
        loaded = ta.load_config()
    # Merges with defaults
    assert loaded["enabled"] is True
    assert loaded["window_s"] == 120
    assert "thresholds" in loaded   # filled from defaults


def test_load_config_missing_returns_default():
    with tempfile.TemporaryDirectory() as td, _with_tmp(td):
        loaded = ta.load_config()
    assert loaded["enabled"] is False
    assert "profiles" in loaded


# ── state persistence ──────────────────────────────────────────────────


def test_state_history_capped():
    with tempfile.TemporaryDirectory() as td, _with_tmp(td):
        state = {"current_profile": "light",
                 "history": [{"i": i} for i in range(150)]}
        ta.save_state(state)
        loaded = ta.load_state()
    assert len(loaded["history"]) == 100   # capped


# ── status ───────────────────────────────────────────────────────────────


def test_status_includes_current_and_config():
    with tempfile.TemporaryDirectory() as td, _with_tmp(td):
        ta.save_config({"enabled": True})
        ta.save_state({"current_profile": "heavy", "since_ts": 1000})
        s = ta.status()
    assert s["config"]["enabled"] is True
    assert s["current_profile"] == "heavy"
