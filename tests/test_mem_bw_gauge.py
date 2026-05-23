"""R&D #26.8 — memory-bandwidth saturation gauge tests."""
import pytest
from unittest.mock import patch
from gpu_dashboard.modules import mem_bw_gauge as mb


# ── classify ───────────────────────────────────────────────────────────


def test_classify_idle():
    v = mb.classify({"gpu_util_mean": 1, "mem_util_mean": 2,
                       "ratio_mem_over_gpu": 2.0})
    assert v["verdict"] == "idle"


def test_classify_balanced():
    v = mb.classify({"gpu_util_mean": 70, "mem_util_mean": 70,
                       "ratio_mem_over_gpu": 1.0})
    assert v["verdict"] == "balanced"


def test_classify_balanced_edges():
    v = mb.classify({"gpu_util_mean": 80, "mem_util_mean": 100,
                       "ratio_mem_over_gpu": 1.25})
    assert v["verdict"] == "balanced"


def test_classify_bandwidth_bound():
    v = mb.classify({"gpu_util_mean": 40, "mem_util_mean": 80,
                       "ratio_mem_over_gpu": 2.0})
    assert v["verdict"] == "bandwidth_bound"
    assert "smaller quant" in v["recommendation"].lower()


def test_classify_compute_bound():
    v = mb.classify({"gpu_util_mean": 90, "mem_util_mean": 30,
                       "ratio_mem_over_gpu": 0.33})
    assert v["verdict"] == "compute_bound"
    assert "Memory speed" in v["recommendation"]


def test_classify_undetermined():
    """gpu_util_mean > 5 not idle, but ratio computed as None."""
    v = mb.classify({"gpu_util_mean": 0, "mem_util_mean": 60,
                       "ratio_mem_over_gpu": None})
    # gpu_util 0 < 5 AND mem 60 > 5 → not 'idle' threshold, falls through
    # to ratio==None → 'undetermined'
    assert v["verdict"] == "undetermined"


# ── sample_window (mocked I/O) ─────────────────────────────────────────


def test_sample_window_aggregates():
    """3 samples per GPU → mean is correct."""
    samples = [
        {"rows": [{"index": 0, "gpu_util_pct": 50, "mem_util_pct": 80}],
         "ts": 100},
        {"rows": [{"index": 0, "gpu_util_pct": 60, "mem_util_pct": 90}],
         "ts": 101},
        {"rows": [{"index": 0, "gpu_util_pct": 40, "mem_util_pct": 70}],
         "ts": 102},
    ]
    with patch.object(mb, "query_utilization_pair",
                       side_effect=samples + [None]):
        with patch.object(mb.time, "sleep"):
            w = mb.sample_window(n=3, interval_s=0)
    agg = w["per_gpu"][0]
    assert agg["index"] == 0
    assert agg["gpu_util_mean"] == 50.0  # (50+60+40)/3
    assert agg["mem_util_mean"] == 80.0  # (80+90+70)/3
    assert agg["ratio_mem_over_gpu"] == 1.6


def test_sample_window_handles_none_samples():
    """If nvidia-smi fails some samples, just skip them."""
    samples = [None, {"rows": [{"index": 0, "gpu_util_pct": 50,
                                  "mem_util_pct": 60}], "ts": 1}, None]
    with patch.object(mb, "query_utilization_pair",
                       side_effect=samples + [None] * 10):
        with patch.object(mb.time, "sleep"):
            w = mb.sample_window(n=3, interval_s=0)
    assert len(w["per_gpu"]) == 1
    assert w["per_gpu"][0]["sample_count"] == 1


def test_sample_window_multi_gpu():
    samples = [
        {"rows": [
            {"index": 0, "gpu_util_pct": 50, "mem_util_pct": 80},
            {"index": 1, "gpu_util_pct": 90, "mem_util_pct": 30},
        ], "ts": 1},
    ]
    with patch.object(mb, "query_utilization_pair",
                       side_effect=samples + [None]):
        with patch.object(mb.time, "sleep"):
            w = mb.sample_window(n=1, interval_s=0)
    assert len(w["per_gpu"]) == 2


def test_sample_window_zero_compute_yields_none_ratio():
    samples = [{"rows": [{"index": 0, "gpu_util_pct": 0,
                            "mem_util_pct": 50}], "ts": 1}]
    with patch.object(mb, "query_utilization_pair",
                       side_effect=samples + [None]):
        with patch.object(mb.time, "sleep"):
            w = mb.sample_window(n=1, interval_s=0)
    assert w["per_gpu"][0]["ratio_mem_over_gpu"] is None


# ── status ─────────────────────────────────────────────────────────────


def test_status_no_smi():
    with patch.object(mb, "sample_window",
                       return_value={"per_gpu": [], "total_samples": 0}):
        s = mb.status()
    assert s["ok"] is False


def test_status_balanced_workload():
    with patch.object(mb, "sample_window",
                       return_value={
                           "per_gpu": [{"index": 0,
                                         "gpu_util_mean": 70,
                                         "mem_util_mean": 65,
                                         "ratio_mem_over_gpu": 0.93,
                                         "sample_count": 5}],
                           "total_samples": 5,
                       }):
        s = mb.status()
    assert s["ok"] is True
    assert s["per_gpu"][0]["verdict"]["verdict"] == "balanced"


def test_status_bandwidth_bound_workload():
    with patch.object(mb, "sample_window",
                       return_value={
                           "per_gpu": [{"index": 0,
                                         "gpu_util_mean": 30,
                                         "mem_util_mean": 85,
                                         "ratio_mem_over_gpu": 2.83,
                                         "sample_count": 5}],
                           "total_samples": 5,
                       }):
        s = mb.status()
    assert s["per_gpu"][0]["verdict"]["verdict"] == "bandwidth_bound"


def test_status_idle():
    with patch.object(mb, "sample_window",
                       return_value={
                           "per_gpu": [{"index": 0,
                                         "gpu_util_mean": 1,
                                         "mem_util_mean": 2,
                                         "ratio_mem_over_gpu": 2.0,
                                         "sample_count": 5}],
                           "total_samples": 5,
                       }):
        s = mb.status()
    assert s["per_gpu"][0]["verdict"]["verdict"] == "idle"


def test_status_uses_cfg_overrides():
    with patch.object(mb, "sample_window",
                       return_value={"per_gpu": [], "total_samples": 0}) as mock_sw:
        mb.status(cfg={"BW_GAUGE_SAMPLES": "3", "BW_GAUGE_INTERVAL_S": "0.2"})
    mock_sw.assert_called_once_with(n=3, interval_s=0.2)


def test_status_clamps_cfg():
    """Reject out-of-range cfg values."""
    with patch.object(mb, "sample_window",
                       return_value={"per_gpu": [], "total_samples": 0}) as mock_sw:
        mb.status(cfg={"BW_GAUGE_SAMPLES": "1000",
                        "BW_GAUGE_INTERVAL_S": "999"})
    args = mock_sw.call_args
    assert args.kwargs["n"] <= 20
    assert args.kwargs["interval_s"] <= 2.0
