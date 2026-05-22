"""R&D #8.4 — llama-bench monitor tests."""
import json
import subprocess
from unittest.mock import patch
from gpu_dashboard.modules import llama_bench as lb


class FakeProc:
    def __init__(self, stdout="", returncode=0):
        self.stdout = stdout
        self.returncode = returncode


# ── parse_output ──────────────────────────────────────────────────────────


def test_parse_output_empty_input():
    assert lb.parse_output("") == []
    assert lb.parse_output("not json") == []


def test_parse_output_valid_single_run():
    sample = json.dumps([{
        "build_commit": "abc1234",
        "model_filename": "/home/x/models/test.gguf",
        "model_type": "qwen2",
        "n_params": 7400000000,
        "n_threads": 8,
        "n_gpu_layers": 99,
        "n_batch": 2048,
        "test": "pp512",
        "n_prompt": 512, "n_gen": 0,
        "avg_ts": 256.7, "stddev_ts": 1.5,
    }])
    out = lb.parse_output(sample)
    assert len(out) == 1
    assert out[0]["model"] == "test.gguf"  # basename only
    assert out[0]["test"] == "pp512"
    assert out[0]["avg_ts"] == 256.7
    assert out[0]["build_commit"] == "abc1234"


def test_parse_output_multi_test_pp_and_tg():
    """Typical bench produces pp512 + tg128 results."""
    sample = json.dumps([
        {"model_filename": "m.gguf", "test": "pp512", "n_prompt": 512, "n_gen": 0,
         "avg_ts": 1500.0, "stddev_ts": 5.0, "build_commit": "abc"},
        {"model_filename": "m.gguf", "test": "tg128", "n_prompt": 0, "n_gen": 128,
         "avg_ts": 95.5, "stddev_ts": 0.5, "build_commit": "abc"},
    ])
    out = lb.parse_output(sample)
    assert len(out) == 2
    tests = {r["test"] for r in out}
    assert tests == {"pp512", "tg128"}


def test_parse_output_missing_fields_uses_defaults():
    """Bench rows with missing fields shouldn't crash, default to 0."""
    sample = json.dumps([{"model_filename": "m.gguf"}])
    out = lb.parse_output(sample)
    assert out[0]["test"] == ""
    assert out[0]["avg_ts"] == 0.0


# ── find_binary ──────────────────────────────────────────────────────────


def test_find_binary_returns_none_when_absent():
    """When no llama-bench is on PATH or known hints."""
    import os as _os
    with patch.object(_os.path, "isfile", return_value=False), \
         patch.object(subprocess, "run", return_value=FakeProc(returncode=1)):
        assert lb.find_binary() is None


# ── detect_regression ────────────────────────────────────────────────────


def test_detect_regression_returns_none_if_too_few_runs():
    assert lb.detect_regression([]) is None
    assert lb.detect_regression([{"avg_ts": 100}, {"avg_ts": 101}]) is None  # < 3


def test_detect_regression_no_regression_when_stable():
    runs = [{"avg_ts": 100.0} for _ in range(8)]
    result = lb.detect_regression(runs)
    assert result is not None
    assert result["regression"] is False
    assert abs(result["delta_pct"]) < 0.01


def test_detect_regression_flags_drop_above_threshold():
    """Baseline ~100, latest 90 = -10% → triggers (threshold 5%)."""
    runs = [{"avg_ts": 100.0} for _ in range(7)] + [{"avg_ts": 90.0}]
    result = lb.detect_regression(runs, threshold_pct=5.0)
    assert result["regression"] is True
    assert result["delta_pct"] == -10.0


def test_detect_regression_small_drop_below_threshold():
    """Baseline ~100, latest 97 = -3% → below 5% threshold, NOT flagged."""
    runs = [{"avg_ts": 100.0} for _ in range(7)] + [{"avg_ts": 97.0}]
    result = lb.detect_regression(runs, threshold_pct=5.0)
    assert result["regression"] is False
    assert result["delta_pct"] == -3.0


def test_detect_regression_upward_change_never_flagged():
    """Improvement should never count as regression."""
    runs = [{"avg_ts": 100.0} for _ in range(7)] + [{"avg_ts": 115.0}]
    result = lb.detect_regression(runs, threshold_pct=5.0)
    assert result["regression"] is False
    assert result["delta_pct"] == 15.0


def test_detect_regression_uses_median_not_mean():
    """One outlier high in baseline shouldn't shift mean wildly."""
    # baseline has 7 values around 100 with one outlier 200
    runs = [
        {"avg_ts": 100}, {"avg_ts": 102}, {"avg_ts": 98}, {"avg_ts": 200},
        {"avg_ts": 101}, {"avg_ts": 99}, {"avg_ts": 100},
        {"avg_ts": 90},  # latest = 90
    ]
    result = lb.detect_regression(runs, threshold_pct=5.0)
    # Median of first 7 ≈ 100, latest 90 = -10% → flagged
    assert result["regression"] is True
