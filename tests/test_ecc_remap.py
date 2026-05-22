"""R&D #17.1 — ECC remap scrubber tests."""
import os
import json
import subprocess
import tempfile
import pytest
from unittest.mock import patch
from gpu_dashboard.modules import ecc_remap as er


def _with_tmp(td):
    return patch.object(er, "history_path",
                        return_value=os.path.join(td, "history.json"))


# ── _parse_int_or_na ────────────────────────────────────────────────────


def test_parse_int_returns_int():
    assert er._parse_int_or_na("0") == 0
    assert er._parse_int_or_na("12") == 12


def test_parse_int_na_returns_none():
    assert er._parse_int_or_na("[N/A]") is None
    assert er._parse_int_or_na("N/A") is None
    assert er._parse_int_or_na("") is None


# ── probe ────────────────────────────────────────────────────────────────


def test_probe_no_nvidia_smi():
    with patch.object(subprocess, "run", side_effect=FileNotFoundError):
        assert er.probe() == []


def test_probe_consumer_card_returns_na():
    """RTX 3090 returns [N/A] for all counters."""
    class FakeProc:
        stdout = "GPU-12345, NVIDIA GeForce RTX 3090, [N/A], [N/A], [N/A], [N/A], [N/A], [N/A], [N/A], [N/A], [N/A]\n"
        returncode = 0
    with patch.object(subprocess, "run", return_value=FakeProc()):
        r = er.probe()
    assert len(r) == 1
    assert r[0]["uuid"] == "GPU-12345"
    assert r[0]["correctable"] is None
    assert r[0]["available"] is False


def test_probe_datacenter_card_parses_counters():
    """A100 returns real counts."""
    class FakeProc:
        stdout = "GPU-A100-x, NVIDIA A100, 3, 8, 0, 0, 0, 1, 2, 3, 60\n"
        returncode = 0
    with patch.object(subprocess, "run", return_value=FakeProc()):
        r = er.probe()
    assert r[0]["correctable"] == 3
    assert r[0]["uncorrectable"] == 8
    assert r[0]["available"] is True


# ── _verdict ─────────────────────────────────────────────────────────────


def test_verdict_ok_when_low():
    assert er._verdict(unc=2, failure=0)["kind"] == "ok"


def test_verdict_warn_at_5():
    v = er._verdict(unc=5, failure=0)
    assert v["kind"] == "warn"
    assert ">= 5" in v["reason"]


def test_verdict_warn_at_19():
    assert er._verdict(unc=19, failure=0)["kind"] == "warn"


def test_verdict_fail_at_20():
    v = er._verdict(unc=20, failure=0)
    assert v["kind"] == "fail"
    assert ">= 20" in v["reason"]


def test_verdict_fail_when_failure_count_nonzero():
    v = er._verdict(unc=0, failure=1)
    assert v["kind"] == "fail"
    assert "failure" in v["reason"]


def test_verdict_skip_when_na():
    """Consumer card → no data → skip."""
    assert er._verdict(unc=None, failure=None)["kind"] == "skip"


# ── load / save history ──────────────────────────────────────────────────


def test_load_history_missing():
    with tempfile.TemporaryDirectory() as td, _with_tmp(td):
        assert er.load_history() == []


def test_save_then_load_history():
    with tempfile.TemporaryDirectory() as td, _with_tmp(td):
        rows = [{"ts": 100, "uuid": "x", "correctable": 1}]
        er.save_history(rows)
        loaded = er.load_history()
    assert loaded == rows


def test_history_caps_at_max():
    with tempfile.TemporaryDirectory() as td, _with_tmp(td):
        rows = [{"ts": i, "uuid": "x", "correctable": i} for i in range(er._HISTORY_MAX + 50)]
        er.save_history(rows)
        loaded = er.load_history()
    assert len(loaded) == er._HISTORY_MAX
    # Newest preserved
    assert loaded[-1]["ts"] == er._HISTORY_MAX + 49


# ── record_snapshot ──────────────────────────────────────────────────────


def test_record_first_snapshot_no_delta():
    with tempfile.TemporaryDirectory() as td, _with_tmp(td):
        probe_data = [{"uuid": "U1", "name": "A100",
                        "correctable": 3, "uncorrectable": 1, "pending": 0,
                        "failure": 0, "histogram": {}, "available": True}]
        r = er.record_snapshot(probe_data)
    assert len(r["snapshots"]) == 1
    assert r["snapshots"][0]["deltas"] == {}  # no previous snapshot


def test_record_second_snapshot_computes_deltas():
    with tempfile.TemporaryDirectory() as td, _with_tmp(td):
        probe1 = [{"uuid": "U1", "name": "A100",
                    "correctable": 3, "uncorrectable": 1, "pending": 0,
                    "failure": 0, "histogram": {}, "available": True}]
        probe2 = [{"uuid": "U1", "name": "A100",
                    "correctable": 5, "uncorrectable": 4, "pending": 0,
                    "failure": 0, "histogram": {}, "available": True}]
        er.record_snapshot(probe1)
        r = er.record_snapshot(probe2)
    snap = r["snapshots"][0]
    assert snap["deltas"]["correctable"] == 2
    assert snap["deltas"]["uncorrectable"] == 3


def test_record_includes_verdict():
    with tempfile.TemporaryDirectory() as td, _with_tmp(td):
        probe_data = [{"uuid": "U1", "name": "A100",
                        "correctable": 0, "uncorrectable": 25, "pending": 0,
                        "failure": 0, "histogram": {}, "available": True}]
        r = er.record_snapshot(probe_data)
    assert r["snapshots"][0]["verdict"]["kind"] == "fail"


# ── rma_report_csv ───────────────────────────────────────────────────────


def test_rma_csv_has_header():
    csv = er.rma_report_csv(history=[])
    assert "uuid" in csv
    assert "uncorrectable" in csv
    assert "verdict" in csv


def test_rma_csv_aggregates_per_uuid():
    history = [
        {"ts": 100, "uuid": "U1", "name": "A100", "correctable": 1,
         "uncorrectable": 0, "pending": 0, "failure": 0,
         "verdict": {"kind": "ok"}},
        {"ts": 200, "uuid": "U1", "name": "A100", "correctable": 5,
         "uncorrectable": 3, "pending": 0, "failure": 0,
         "verdict": {"kind": "ok"}},
        {"ts": 150, "uuid": "U2", "name": "A100", "correctable": 0,
         "uncorrectable": 0, "pending": 0, "failure": 0,
         "verdict": {"kind": "ok"}},
    ]
    csv = er.rma_report_csv(history=history)
    lines = csv.strip().splitlines()
    # 1 header + 2 unique UUIDs = 3 rows
    assert len(lines) == 3


# ── status ───────────────────────────────────────────────────────────────


def test_status_returns_live_and_history():
    with tempfile.TemporaryDirectory() as td, _with_tmp(td), \
         patch.object(er, "probe", return_value=[{"uuid": "X", "name": "RTX 3090",
                                                    "correctable": None, "uncorrectable": None,
                                                    "pending": None, "failure": None,
                                                    "histogram": {}, "available": False}]):
        s = er.status()
    assert s["ok"] is True
    assert len(s["live"]) == 1
    assert s["any_card_exposes_ecc"] is False
