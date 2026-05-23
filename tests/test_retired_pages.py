"""R&D #25.1 — retired-page trend tests."""
import os
import pytest
from unittest.mock import patch
from gpu_dashboard.modules import retired_pages as rp


def _with_baseline(td):
    return patch.object(rp, "baseline_path",
                        lambda: os.path.join(td, "retired_baseline.json"))


# ── parse_retired_csv ──────────────────────────────────────────────────


def test_parse_basic():
    csv = ("GPU-abc, 0x12345, Single Bit ECC, 2024-01-01 10:00:00.000\n"
            "GPU-abc, 0x67890, Double Bit ECC, 2024-01-02 11:00:00.000\n")
    out = rp.parse_retired_csv(csv)
    assert len(out) == 2
    assert out[0]["gpu_uuid"] == "GPU-abc"
    assert "Single" in out[0]["cause"]


def test_parse_no_data_lines():
    csv = "No retired pages found.\n"
    assert rp.parse_retired_csv(csv) == []


def test_parse_empty():
    assert rp.parse_retired_csv("") == []


def test_parse_skips_garbage():
    csv = "garbage\nGPU-x, 0x1, SBE, ts\n"
    out = rp.parse_retired_csv(csv)
    assert len(out) == 1


# ── aggregate_by_cause ─────────────────────────────────────────────────


def test_aggregate_sbe_dbe():
    entries = [
        {"gpu_uuid": "GPU-A", "address": "0x1", "cause": "Single Bit ECC"},
        {"gpu_uuid": "GPU-A", "address": "0x2", "cause": "Double Bit ECC"},
        {"gpu_uuid": "GPU-A", "address": "0x3", "cause": "Single Bit ECC"},
    ]
    out = rp.aggregate_by_cause(entries)
    assert out["GPU-A"]["sbe"] == 2
    assert out["GPU-A"]["dbe"] == 1
    assert out["GPU-A"]["total"] == 3


def test_aggregate_multi_gpu():
    entries = [
        {"gpu_uuid": "GPU-A", "address": "0x1", "cause": "SBE"},
        {"gpu_uuid": "GPU-B", "address": "0x1", "cause": "DBE"},
    ]
    out = rp.aggregate_by_cause(entries)
    assert "GPU-A" in out
    assert "GPU-B" in out


def test_aggregate_empty():
    assert rp.aggregate_by_cause([]) == {}


# ── _verdict_for_gpu ───────────────────────────────────────────────────


def test_verdict_clean():
    v = rp._verdict_for_gpu({"sbe": 0, "dbe": 0, "total": 0,
                              "entries": []}, 0, 0)
    assert v["severity"] == "info"
    assert v["label"] == "clean"


def test_verdict_dbe_critical():
    v = rp._verdict_for_gpu({"sbe": 0, "dbe": 1, "total": 1,
                              "entries": []}, 0, 1)
    assert v["severity"] == "critical"
    assert v["label"] == "dbe_present"
    assert "RMA" in v["recommendation"]


def test_verdict_sbe_growth():
    v = rp._verdict_for_gpu({"sbe": 10, "dbe": 0, "total": 10,
                              "entries": []}, 5, 0)
    assert v["severity"] == "warn"
    assert v["label"] == "sbe_growth"


def test_verdict_sbe_stable():
    v = rp._verdict_for_gpu({"sbe": 5, "dbe": 0, "total": 5,
                              "entries": []}, 0, 0)
    assert v["severity"] == "info"
    assert v["label"] == "sbe_stable"


def test_verdict_sbe_low_growth():
    v = rp._verdict_for_gpu({"sbe": 6, "dbe": 0, "total": 6,
                              "entries": []}, 2, 0)
    assert v["severity"] == "info"
    assert v["label"] == "sbe_growing"


# ── classify integration ──────────────────────────────────────────────


def test_classify_seeds_baseline():
    by_gpu = {"GPU-A": {"sbe": 3, "dbe": 0, "total": 3, "entries": []}}
    out = rp.classify(by_gpu, baseline={}, now_ts=1000.0)
    assert out["per_gpu"][0]["first_seen"] is True
    assert "GPU-A" in out["new_baseline"]


def test_classify_detects_dbe_critical():
    by_gpu = {"GPU-A": {"sbe": 0, "dbe": 1, "total": 1, "entries": []}}
    out = rp.classify(by_gpu, baseline={"GPU-A": {"sbe": 0, "dbe": 0}},
                       now_ts=2000.0)
    assert out["worst_severity"] == "critical"


def test_classify_delta_growth():
    by_gpu = {"GPU-A": {"sbe": 20, "dbe": 0, "total": 20, "entries": []}}
    out = rp.classify(by_gpu,
                       baseline={"GPU-A": {"sbe": 10, "dbe": 0}},
                       now_ts=2000.0)
    assert out["per_gpu"][0]["delta_sbe"] == 10


# ── status ─────────────────────────────────────────────────────────────


def test_status_unsupported(tmp_path):
    with _with_baseline(str(tmp_path)):
        with patch.object(rp, "query_retired_pages", return_value=None):
            s = rp.status()
    assert s["ok"] is False
    assert s["supported"] is False


def test_status_clean_gpu(tmp_path):
    with _with_baseline(str(tmp_path)):
        with patch.object(rp, "query_retired_pages", return_value=[]):
            s = rp.status()
    assert s["ok"] is True
    assert s["supported"] is True
    assert s["per_gpu"] == []


def test_status_caught_dbe(tmp_path):
    fake = [
        {"gpu_uuid": "GPU-A", "address": "0x1",
         "cause": "Double Bit ECC", "timestamp": ""},
    ]
    with _with_baseline(str(tmp_path)):
        with patch.object(rp, "query_retired_pages", return_value=fake):
            s = rp.status()
    assert s["worst_severity"] == "critical"
    assert s["total_entries"] == 1
