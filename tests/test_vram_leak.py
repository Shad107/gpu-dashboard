"""R&D #22.3 — Per-process VRAM leak detector tests."""
import os
import tempfile
import time
import pytest
from unittest.mock import patch
from gpu_dashboard.modules import vram_leak as vl


def _with_history(td):
    return patch.object(vl, "history_path",
                        lambda: os.path.join(td, "vram_history.json"))


# ── linear_slope ───────────────────────────────────────────────────────


def test_slope_too_few_samples():
    assert vl.linear_slope([]) is None
    assert vl.linear_slope([{"ts": 0, "vram_mib": 1000}]) is None
    assert vl.linear_slope([{"ts": 0, "vram_mib": 1000},
                              {"ts": 60, "vram_mib": 1010}]) is None


def test_slope_flat_returns_zero():
    samples = [{"ts": i, "vram_mib": 1000} for i in range(0, 600, 60)]
    s = vl.linear_slope(samples)
    assert s is not None
    assert abs(s) < 0.01


def test_slope_growing_one_mib_per_minute():
    """Grow 1 MiB per minute → 60 MiB/h slope."""
    samples = [{"ts": i, "vram_mib": 1000 + i // 60}
                for i in range(0, 600, 60)]
    s = vl.linear_slope(samples)
    assert s is not None
    assert abs(s - 60) < 1


def test_slope_constant_ts_returns_none():
    """All ts identical → denominator zero."""
    samples = [{"ts": 0, "vram_mib": 1000 + i} for i in range(5)]
    assert vl.linear_slope(samples) is None


# ── classify ───────────────────────────────────────────────────────────


def test_classify_warming_no_slope():
    v = vl.classify(slope_mib_per_h=None, current_mib=1000)
    assert v["verdict"] == "warming"


def test_classify_stable():
    v = vl.classify(slope_mib_per_h=2.0, current_mib=10000)
    assert v["verdict"] == "stable"


def test_classify_growing():
    """20 MiB/h on a 10 GB process is growing but not leaking."""
    v = vl.classify(slope_mib_per_h=20.0, current_mib=10000)
    assert v["verdict"] == "growing"
    assert v["projected_oom_minutes"] is None


def test_classify_leaking():
    """100 MiB/h on a 10 GB process is leaking."""
    v = vl.classify(slope_mib_per_h=100.0, current_mib=10000)
    assert v["verdict"] == "leaking"
    assert v["projected_oom_minutes"] is not None
    # OOM at 24 GiB ceiling → (24576-10000) / 100 * 60 = ~8740 minutes
    assert v["projected_oom_minutes"] > 1000


def test_classify_leaking_high_growth_pct():
    """6% growth/h triggers leaking even at lower slope."""
    # 60 MiB/h on 1000 MiB process → 6%/h
    v = vl.classify(slope_mib_per_h=60.0, current_mib=1000)
    assert v["verdict"] == "leaking"


def test_classify_leaking_oom_projection_format():
    v = vl.classify(slope_mib_per_h=10000.0, current_mib=20000)
    # Very fast leak, headroom = 4576 MiB, OOM in ~27 minutes
    assert v["projected_oom_minutes"] is not None
    assert v["projected_oom_minutes"] < 60


# ── record_samples ─────────────────────────────────────────────────────


def test_record_creates_history(tmp_path):
    with _with_history(str(tmp_path)):
        samples = [{"pid": 100, "comm": "ollama", "vram_mib": 5000}]
        vl.record_samples(samples, now_ts=1000.0)
        hist = vl.load_history()
    assert "100" in hist
    assert hist["100"]["samples"][0]["vram_mib"] == 5000


def test_record_appends(tmp_path):
    with _with_history(str(tmp_path)):
        vl.record_samples([{"pid": 100, "comm": "ollama", "vram_mib": 5000}],
                          now_ts=1000.0)
        vl.record_samples([{"pid": 100, "comm": "ollama", "vram_mib": 5100}],
                          now_ts=1060.0)
        hist = vl.load_history()
    assert len(hist["100"]["samples"]) == 2


def test_record_caps_per_pid(tmp_path):
    with _with_history(str(tmp_path)):
        for i in range(vl._SAMPLES_PER_PID_MAX + 50):
            vl.record_samples(
                [{"pid": 100, "comm": "ollama", "vram_mib": 5000 + i}],
                now_ts=1000.0 + i * 60,
            )
        hist = vl.load_history()
    assert len(hist["100"]["samples"]) == vl._SAMPLES_PER_PID_MAX
    # Newest retained
    assert hist["100"]["samples"][-1]["vram_mib"] == \
           5000 + vl._SAMPLES_PER_PID_MAX + 49


def test_record_prunes_stale_pids(tmp_path):
    """If a PID hasn't appeared in 24 h, drop it on next record."""
    with _with_history(str(tmp_path)):
        # Seed a stale PID, last sample 2 days ago
        old_ts = time.time() - 2 * 86400
        vl.save_history({"999": {"comm": "old", "samples": [
            {"ts": int(old_ts), "vram_mib": 100}]}})
        # Record a new PID
        vl.record_samples([{"pid": 100, "comm": "ollama", "vram_mib": 5000}])
        hist = vl.load_history()
    assert "100" in hist
    assert "999" not in hist


# ── analyze_history ───────────────────────────────────────────────────


def test_analyze_skips_pids_outside_window():
    hist = {
        "100": {"comm": "ollama", "samples": [
            {"ts": 100, "vram_mib": 1000},  # outside window
        ]},
    }
    out = vl.analyze_history(hist, window_s=60, now_ts=10000.0)
    assert out == []


def test_analyze_reports_growing_pid():
    samples = [{"ts": 1000 + i * 60, "vram_mib": 1000 + i * 30}
                for i in range(10)]
    # 30 MiB/min = 1800 MiB/h → leaking
    hist = {"100": {"comm": "leaker", "samples": samples}}
    out = vl.analyze_history(hist, window_s=10000, now_ts=2000.0)
    assert len(out) == 1
    assert out[0]["verdict"]["verdict"] == "leaking"


def test_analyze_handles_multiple_pids():
    flat = [{"ts": 1000 + i * 60, "vram_mib": 5000} for i in range(10)]
    growing = [{"ts": 1000 + i * 60, "vram_mib": 5000 + i * 100}
                for i in range(10)]
    hist = {
        "100": {"comm": "stable", "samples": flat},
        "200": {"comm": "leaky", "samples": growing},
    }
    out = vl.analyze_history(hist, window_s=10000, now_ts=2000.0)
    by_pid = {p["pid"]: p for p in out}
    assert by_pid[100]["verdict"]["verdict"] == "stable"
    assert by_pid[200]["verdict"]["verdict"] in ("growing", "leaking")


# ── status ────────────────────────────────────────────────────────────


def test_status_with_no_processes(tmp_path):
    with _with_history(str(tmp_path)):
        with patch.object(vl, "sample_now", return_value=[]):
            s = vl.status()
    assert s["process_count"] == 0
    assert s["leaking_count"] == 0


def test_status_seeds_history(tmp_path):
    with _with_history(str(tmp_path)):
        with patch.object(vl, "sample_now",
                          return_value=[{"pid": 100, "comm": "x",
                                          "vram_mib": 5000}]):
            s = vl.status()
        hist = vl.load_history()
    assert "100" in hist
    # 1 sample → warming
    assert s["process_count"] == 1


def test_status_uses_window_config(tmp_path):
    with _with_history(str(tmp_path)):
        with patch.object(vl, "sample_now", return_value=[]):
            s = vl.status(cfg={"VRAM_LEAK_WINDOW_S": "60"})
    assert s["window_s"] == 60
