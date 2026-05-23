"""R&D #24.2 — PCIe AER counter tests."""
import os
import pytest
from unittest.mock import patch
from gpu_dashboard.modules import pcie_aer as pa


def _with_baseline(td):
    return patch.object(pa, "baseline_path",
                        lambda: os.path.join(td, "pcie_aer_baseline.json"))


# ── parse_aer_file ─────────────────────────────────────────────────────


_REAL_CORRECTABLE = """\
RxErr 0
BadTLP 5
BadDLLP 0
Rollover 0
Timeout 0
NonFatalErr 0
CorrIntErr 0
HeaderOF 0
TOTAL_ERR_COR 5
"""


def test_parse_real_correctable():
    d = pa.parse_aer_file(_REAL_CORRECTABLE)
    assert d["BadTLP"] == 5
    assert d["TOTAL_ERR_COR"] == 5
    assert d["RxErr"] == 0


def test_parse_empty():
    assert pa.parse_aer_file("") == {}


def test_parse_skips_garbage():
    d = pa.parse_aer_file("BadTLP 3\nGarbage line without number\n")
    assert d == {"BadTLP": 3}


# ── total_for_tier ─────────────────────────────────────────────────────


def test_total_uses_total_field():
    d = {"BadTLP": 5, "RxErr": 3, "TOTAL_ERR_COR": 10}
    assert pa.total_for_tier(d) == 10


def test_total_sums_if_no_total_field():
    d = {"BadTLP": 5, "RxErr": 3}
    assert pa.total_for_tier(d) == 8


def test_total_empty():
    assert pa.total_for_tier({}) == 0


# ── list_nvidia_bdfs ───────────────────────────────────────────────────


def test_list_nvidia_bdfs(tmp_path):
    n = tmp_path / "0000:01:00.0"; n.mkdir()
    (n / "vendor").write_text("0x10de\n")
    a = tmp_path / "0000:02:00.0"; a.mkdir()
    (a / "vendor").write_text("0x1002\n")
    out = pa.list_nvidia_bdfs(sys_root=str(tmp_path))
    assert out == ["0000:01:00.0"]


def test_list_nvidia_bdfs_empty(tmp_path):
    assert pa.list_nvidia_bdfs(sys_root=str(tmp_path)) == []


# ── compute_delta ──────────────────────────────────────────────────────


def test_delta_no_change():
    prev = {"correctable": {"BadTLP": 5, "TOTAL_ERR_COR": 5},
            "fatal": {}, "nonfatal": {}}
    curr = prev
    assert pa.compute_delta(prev, curr) == {
        "correctable": {}, "fatal": {}, "nonfatal": {}}


def test_delta_correctable_grew():
    prev = {"correctable": {"BadTLP": 5, "TOTAL_ERR_COR": 5}}
    curr = {"correctable": {"BadTLP": 8, "TOTAL_ERR_COR": 8}}
    d = pa.compute_delta(prev, curr)
    assert d["correctable"]["BadTLP"] == 3
    assert d["correctable"]["TOTAL_ERR_COR"] == 3


def test_delta_skips_negative():
    """Counter regressed (rare) — ignore that field."""
    prev = {"correctable": {"BadTLP": 10}}
    curr = {"correctable": {"BadTLP": 5}}
    assert pa.compute_delta(prev, curr) == {
        "correctable": {}, "fatal": {}, "nonfatal": {}}


def test_delta_new_field_added():
    prev = {"correctable": {}}
    curr = {"correctable": {"RxErr": 3}}
    d = pa.compute_delta(prev, curr)
    assert d["correctable"]["RxErr"] == 3


# ── classify ───────────────────────────────────────────────────────────


def test_classify_clean():
    v = pa.classify({"correctable": {}, "fatal": {}, "nonfatal": {}})
    assert v["verdict"] == "clean"


def test_classify_low_correctable():
    v = pa.classify({"correctable": {"BadTLP": 3, "TOTAL_ERR_COR": 3},
                       "fatal": {}, "nonfatal": {}})
    assert v["verdict"] == "low_correctable"


def test_classify_high_correctable():
    v = pa.classify({"correctable": {"BadTLP": 50, "TOTAL_ERR_COR": 50},
                       "fatal": {}, "nonfatal": {}})
    assert v["verdict"] == "high_correctable"
    assert "cable" in v["recovery"].lower() or "riser" in v["recovery"].lower()


def test_classify_non_fatal_overrides_correctable():
    v = pa.classify({"correctable": {"BadTLP": 5, "TOTAL_ERR_COR": 5},
                       "fatal": {},
                       "nonfatal": {"TLP": 1, "TOTAL_ERR_NONFATAL": 1}})
    assert v["verdict"] == "non_fatal"


def test_classify_fatal_overrides_all():
    v = pa.classify({"correctable": {"TOTAL_ERR_COR": 100},
                       "nonfatal": {"TOTAL_ERR_NONFATAL": 5},
                       "fatal": {"TLP": 1, "TOTAL_ERR_FATAL": 1}})
    assert v["verdict"] == "fatal"


# ── status ─────────────────────────────────────────────────────────────


def test_status_no_devices(tmp_path):
    with _with_baseline(str(tmp_path)):
        with patch.object(pa, "list_nvidia_bdfs", return_value=[]):
            s = pa.status()
    assert s["device_count"] == 0
    assert s["verdict"]["verdict"] == "no_gpus"


def test_status_seeds_baseline(tmp_path):
    """First call : no delta, but baseline persisted."""
    with _with_baseline(str(tmp_path)):
        with patch.object(pa, "list_nvidia_bdfs",
                          return_value=["0000:01:00.0"]):
            with patch.object(pa, "read_aer_counters",
                              return_value={
                                  "correctable": {"TOTAL_ERR_COR": 5},
                                  "fatal": {},
                                  "nonfatal": {},
                              }):
                s = pa.status()
                base = pa.load_baseline()
    assert s["devices"][0]["first_seen"] is True
    assert s["verdict"]["verdict"] == "clean"
    assert "0000:01:00.0" in base


def test_status_detects_growth(tmp_path):
    """Seed at 5 ; second call at 20 → high_correctable."""
    with _with_baseline(str(tmp_path)):
        with patch.object(pa, "list_nvidia_bdfs",
                          return_value=["0000:01:00.0"]):
            with patch.object(pa, "read_aer_counters",
                              return_value={
                                  "correctable": {"TOTAL_ERR_COR": 5},
                                  "fatal": {},
                                  "nonfatal": {},
                              }):
                pa.status()  # seed
            with patch.object(pa, "read_aer_counters",
                              return_value={
                                  "correctable": {"TOTAL_ERR_COR": 20},
                                  "fatal": {},
                                  "nonfatal": {},
                              }):
                s2 = pa.status()
    assert s2["verdict"]["verdict"] == "high_correctable"
    assert s2["devices"][0]["delta"]["correctable"]["TOTAL_ERR_COR"] == 15
