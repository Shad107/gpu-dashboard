"""R&D #24.4 — VRAM thermal-pad drift detector tests."""
import os
import time
import pytest
from unittest.mock import patch
from gpu_dashboard.modules import mem_temp_drift as md


def _with_history(td):
    return patch.object(md, "history_path",
                        lambda: os.path.join(td, "mem_temp_history.json"))


# ── median ─────────────────────────────────────────────────────────────


def test_median_empty():
    assert md.median([]) is None


def test_median_odd():
    assert md.median([1, 3, 5]) == 3


def test_median_even():
    assert md.median([1, 2, 3, 4]) == 2.5


def test_median_one():
    assert md.median([42]) == 42


# ── record_sample / load_history ───────────────────────────────────────


def test_record_basic(tmp_path):
    with _with_history(str(tmp_path)):
        md.record_sample("GPU-X", "RTX 3090", 65.0, 80.0, now_ts=1000.0)
        hist = md.load_history()
    assert "GPU-X" in hist
    assert len(hist["GPU-X"]["samples"]) == 1
    assert hist["GPU-X"]["samples"][0]["delta"] == 15.0


def test_record_appends(tmp_path):
    with _with_history(str(tmp_path)):
        md.record_sample("GPU-X", "RTX 3090", 65.0, 80.0, now_ts=1000.0)
        md.record_sample("GPU-X", "RTX 3090", 66.0, 82.0, now_ts=1060.0)
        hist = md.load_history()
    assert len(hist["GPU-X"]["samples"]) == 2


def test_record_skips_when_temp_missing(tmp_path):
    with _with_history(str(tmp_path)):
        md.record_sample("GPU-X", "RTX 3090", 65.0, None, now_ts=1000.0)
        hist = md.load_history()
    assert hist == {}


def test_record_caps_at_max(tmp_path):
    with _with_history(str(tmp_path)):
        for i in range(md._MAX_SAMPLES + 50):
            md.record_sample("GPU-X", "RTX 3090", 50.0, 60.0 + i * 0.1,
                              now_ts=1000.0 + i * 900)
        hist = md.load_history()
    assert len(hist["GPU-X"]["samples"]) == md._MAX_SAMPLES


# ── compute_drift ──────────────────────────────────────────────────────


def test_drift_no_samples():
    out = md.compute_drift([])
    assert out["sample_count"] == 0
    assert out["drift_c"] is None


def test_drift_stable():
    # 5 samples 30 days apart with constant delta
    samples = [
        {"ts": int(time.time() - 30 * 86400) + i * 86400,
         "gpu_t": 60, "mem_t": 75, "delta": 15.0}
        for i in range(31)
    ]
    out = md.compute_drift(samples)
    assert out["drift_c"] == 0.0


def test_drift_pad_degradation():
    """Old samples delta=15, recent samples delta=22 → drift +7°C."""
    now = time.time()
    old = [
        {"ts": int(now - 30 * 86400) + i * 600,
         "gpu_t": 60, "mem_t": 75, "delta": 15.0}
        for i in range(20)
    ]
    new = [
        {"ts": int(now - 3600 + i * 60),  # last hour
         "gpu_t": 60, "mem_t": 82, "delta": 22.0}
        for i in range(20)
    ]
    samples = old + new
    out = md.compute_drift(samples, now_ts=now)
    assert out["drift_c"] is not None
    assert 6 <= out["drift_c"] <= 8


def test_drift_improving():
    now = time.time()
    old = [
        {"ts": int(now - 30 * 86400) + i * 600,
         "gpu_t": 60, "mem_t": 80, "delta": 20.0}
        for i in range(20)
    ]
    new = [
        {"ts": int(now - 3600 + i * 60),
         "gpu_t": 60, "mem_t": 72, "delta": 12.0}
        for i in range(20)
    ]
    out = md.compute_drift(old + new, now_ts=now)
    assert out["drift_c"] < 0


# ── classify ───────────────────────────────────────────────────────────


def test_classify_warming():
    v = md.classify({"drift_c": 0, "sample_count": 5})
    assert v["verdict"] == "warming"


def test_classify_ok():
    v = md.classify({"drift_c": 1.5, "sample_count": 20})
    assert v["verdict"] == "ok"


def test_classify_pad_degraded():
    v = md.classify({"drift_c": 6.0, "sample_count": 50})
    assert v["verdict"] == "pad_degraded"


def test_classify_urgent():
    v = md.classify({"drift_c": 12.0, "sample_count": 50})
    assert v["verdict"] == "urgent"
    assert "repad" in v["reason"]


def test_classify_improving():
    v = md.classify({"drift_c": -3.0, "sample_count": 50})
    assert v["verdict"] == "improving"


def test_classify_no_drift_data():
    v = md.classify({"drift_c": None, "sample_count": 0})
    assert v["verdict"] == "warming"


# ── status ─────────────────────────────────────────────────────────────


def test_status_no_smi(tmp_path):
    with _with_history(str(tmp_path)):
        with patch.object(md, "query_temps", return_value=[]):
            s = md.status()
    assert s["ok"] is False


def test_status_records_first_sample(tmp_path):
    with _with_history(str(tmp_path)):
        with patch.object(md, "query_temps",
                          return_value=[{"uuid": "GPU-X", "name": "RTX 3090",
                                           "gpu_temp_c": 65.0,
                                           "mem_temp_c": 80.0}]):
            s = md.status()
            hist = md.load_history()
    assert s["gpu_count"] == 1
    assert "GPU-X" in hist
    assert s["gpus"][0]["delta_now"] == 15.0


def test_status_summary_picks_worst():
    """Two GPUs : one ok, one urgent → summary = urgent."""
    fake = [
        {"uuid": "A", "name": "GPU0", "gpu_temp_c": 60.0, "mem_temp_c": 75.0},
        {"uuid": "B", "name": "GPU1", "gpu_temp_c": 60.0, "mem_temp_c": 75.0},
    ]
    # Pre-seed history so drift can be computed
    fake_hist = {
        "A": {"name": "GPU0", "samples": [
            {"ts": int(time.time() - 30 * 86400), "gpu_t": 60,
             "mem_t": 75, "delta": 15} for _ in range(20)] +
            [{"ts": int(time.time() - 60), "gpu_t": 60,
              "mem_t": 75, "delta": 15} for _ in range(20)]},
        "B": {"name": "GPU1", "samples": [
            {"ts": int(time.time() - 30 * 86400), "gpu_t": 60,
             "mem_t": 75, "delta": 15} for _ in range(20)] +
            [{"ts": int(time.time() - 60), "gpu_t": 60,
              "mem_t": 88, "delta": 28} for _ in range(20)]},
    }
    import tempfile
    with tempfile.TemporaryDirectory() as td:
        with _with_history(td):
            md.save_baseline = md.save_history  # alias clarity
            md.save_history(fake_hist)
            with patch.object(md, "query_temps", return_value=fake):
                # Sneak the sample in without overwriting the seed
                with patch.object(md, "record_sample",
                                   side_effect=lambda *a, **k: None):
                    s = md.status()
    assert s["summary_verdict"] == "urgent"
