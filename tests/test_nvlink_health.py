"""R&D #28.4 — NVLink CRC / replay tracker tests."""
import os
import pytest
from unittest.mock import patch
from gpu_dashboard.modules import nvlink_health as nh


def _with_baseline(td):
    return patch.object(nh, "baseline_path",
                        lambda: os.path.join(td, "nvlink_baseline.json"))


_REAL_ERROR_OUTPUT = """\
GPU 0: NVIDIA GeForce RTX 3090 (UUID: GPU-aaaa1111-2222-3333-4444-555555555555)
         Link 0: Replay Errors: 0
         Link 0: Recovery Errors: 0
         Link 0: CRC Flit Errors: 5
         Link 0: CRC Data Errors: 0
         Link 1: Replay Errors: 12
         Link 1: Recovery Errors: 0
         Link 1: CRC Flit Errors: 0
GPU 1: NVIDIA GeForce RTX 3090 (UUID: GPU-bbbb2222-3333-4444-5555-666666666666)
         Link 0: Replay Errors: 0
         Link 0: Recovery Errors: 0
         Link 0: CRC Flit Errors: 0
"""

_REAL_STATUS_OUTPUT = """\
GPU 0: NVIDIA GeForce RTX 3090 (UUID: GPU-aaaa1111-2222-3333-4444-555555555555)
         Link 0: 14.062 GB/s
         Link 1: 14.062 GB/s
GPU 1: NVIDIA GeForce RTX 3090 (UUID: GPU-bbbb2222-3333-4444-5555-666666666666)
         Link 0: 14.062 GB/s
"""


# ── parse_error_counters ───────────────────────────────────────────────


def test_parse_two_gpus_two_links():
    d = nh.parse_error_counters(_REAL_ERROR_OUTPUT)
    assert len(d) == 2
    uuid0 = "GPU-aaaa1111-2222-3333-4444-555555555555"
    assert d[uuid0][0]["CRC Flit"] == 5
    assert d[uuid0][1]["Replay"] == 12


def test_parse_empty():
    assert nh.parse_error_counters("") == {}


def test_parse_skips_garbage_values():
    txt = "GPU 0: NVIDIA (UUID: GPU-xxx)\n  Link 0: Replay Errors: notanumber\n"
    d = nh.parse_error_counters(txt)
    assert d.get("GPU-xxx", {}) == {}


# ── parse_link_status ──────────────────────────────────────────────────


def test_parse_link_status_up():
    d = nh.parse_link_status(_REAL_STATUS_OUTPUT)
    uuid0 = "GPU-aaaa1111-2222-3333-4444-555555555555"
    assert d[uuid0][0] == "up"
    assert d[uuid0][1] == "up"


def test_parse_link_status_down():
    txt = ("GPU 0: NVIDIA (UUID: GPU-xxx)\n"
            "         Link 0: Down\n")
    d = nh.parse_link_status(txt)
    assert d["GPU-xxx"][0] == "down"


# ── compute_delta ──────────────────────────────────────────────────────


def test_delta_no_change():
    prev = {"GPU-A": {"0": {"Replay": 5}}}
    curr = {"GPU-A": {0: {"Replay": 5}}}
    assert nh.compute_delta(prev, curr) == {}


def test_delta_growth():
    prev = {"GPU-A": {"0": {"Replay": 5}}}
    curr = {"GPU-A": {0: {"Replay": 12}}}
    out = nh.compute_delta(prev, curr)
    assert out["GPU-A"][0]["Replay"] == 7


def test_delta_skips_negative():
    """Counter regressed — ignore."""
    prev = {"GPU-A": {"0": {"Replay": 50}}}
    curr = {"GPU-A": {0: {"Replay": 10}}}
    assert nh.compute_delta(prev, curr) == {}


# ── classify ───────────────────────────────────────────────────────────


def test_classify_clean():
    v = nh.classify({}, {"GPU-A": {0: "up"}})
    assert v["verdict"] == "clean"


def test_classify_link_down_wins():
    deltas = {"GPU-A": {0: {"CRC Flit": 100}}}
    statuses = {"GPU-A": {0: "down"}}
    v = nh.classify(deltas, statuses)
    assert v["verdict"] == "link_down"
    assert v["link_down_count"] == 1


def test_classify_crc_growth():
    deltas = {"GPU-A": {0: {"CRC Flit": 50}}}
    v = nh.classify(deltas, {"GPU-A": {0: "up"}})
    assert v["verdict"] == "crc_growth"
    assert v["crc_delta"] == 50


def test_classify_replay_growth_only():
    deltas = {"GPU-A": {0: {"Replay": 20}}}
    v = nh.classify(deltas, {"GPU-A": {0: "up"}})
    assert v["verdict"] == "replay_growth"


def test_classify_crc_beats_replay():
    deltas = {"GPU-A": {0: {"Replay": 50, "CRC Flit": 50}}}
    v = nh.classify(deltas, {"GPU-A": {0: "up"}})
    assert v["verdict"] == "crc_growth"


def test_classify_low_replay_clean():
    deltas = {"GPU-A": {0: {"Replay": 5}}}
    v = nh.classify(deltas, {"GPU-A": {0: "up"}})
    assert v["verdict"] == "clean"


# ── status integration ───────────────────────────────────────────────


def test_status_no_smi():
    with patch.object(nh, "query_errors", return_value=None):
        with patch.object(nh, "query_status", return_value=None):
            s = nh.status()
    assert s["ok"] is False


def test_status_no_nvlink(tmp_path):
    with _with_baseline(str(tmp_path)):
        with patch.object(nh, "query_errors", return_value={}):
            with patch.object(nh, "query_status", return_value={}):
                s = nh.status()
    assert s["supported"] is False
    assert s["verdict"]["verdict"] == "no_nvlink"


def test_status_seeds_baseline_then_detects(tmp_path):
    """First call seeds baseline ; second call with growth → growth verdict."""
    with _with_baseline(str(tmp_path)):
        first = {"GPU-A": {0: {"Replay": 0, "CRC Flit": 0}}}
        with patch.object(nh, "query_errors", return_value=first):
            with patch.object(nh, "query_status",
                              return_value={"GPU-A": {0: "up"}}):
                s1 = nh.status()
        assert s1["verdict"]["verdict"] == "clean"
        # Now bump replay to 20
        second = {"GPU-A": {0: {"Replay": 20, "CRC Flit": 0}}}
        with patch.object(nh, "query_errors", return_value=second):
            with patch.object(nh, "query_status",
                              return_value={"GPU-A": {0: "up"}}):
                s2 = nh.status()
        assert s2["verdict"]["verdict"] == "replay_growth"
