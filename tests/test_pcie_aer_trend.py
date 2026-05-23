"""Tests for modules/pcie_aer_trend.py — R&D #38.1 AER trend tracker."""
from __future__ import annotations

from pathlib import Path

import pytest

from gpu_dashboard.modules import pcie_aer_trend


_SAMPLE_AER = """\
RxErr 0
BadTLP 0
BadDLLP 0
Rollover 0
Timeout 0
NonFatalErr 0
CorrIntErr 0
HeaderOF 0
TOTAL_ERR_COR 0
"""


_DIRTY_AER = """\
RxErr 5
BadTLP 2
BadDLLP 1
Rollover 0
Timeout 0
NonFatalErr 0
CorrIntErr 0
HeaderOF 0
TOTAL_ERR_COR 8
"""


def _mk_gpu(root: Path, bdf: str, *, vendor: str = "0x10de",
              klass: str = "0x030000",
              correctable: str = _SAMPLE_AER,
              fatal: str | None = None,
              nonfatal: str | None = None):
    d = root / bdf
    d.mkdir(parents=True)
    (d / "vendor").write_text(vendor + "\n")
    (d / "class").write_text(klass + "\n")
    if correctable is not None:
        (d / "aer_dev_correctable").write_text(correctable)
    if fatal is not None:
        (d / "aer_dev_fatal").write_text(fatal)
    if nonfatal is not None:
        (d / "aer_dev_nonfatal").write_text(nonfatal)


# --- parse_aer_file ----------------------------------------------

def test_parse_aer_file_basic():
    out = pcie_aer_trend.parse_aer_file(_SAMPLE_AER)
    assert out["TOTAL_ERR_COR"] == 0
    assert out["RxErr"] == 0


def test_parse_aer_file_dirty():
    out = pcie_aer_trend.parse_aer_file(_DIRTY_AER)
    assert out["RxErr"] == 5
    assert out["TOTAL_ERR_COR"] == 8


def test_parse_aer_file_empty():
    assert pcie_aer_trend.parse_aer_file("") == {}


def test_parse_aer_file_skips_garbage():
    txt = "valid 5\ngarbage_line\nalso_valid 10\n"
    out = pcie_aer_trend.parse_aer_file(txt)
    assert out == {"valid": 5, "also_valid": 10}


# --- classify ----------------------------------------------------

def test_classify_clean():
    cards = [{"gpu_bdf": "0000:01:00.0",
              "correctable": {"TOTAL_ERR_COR": 0},
              "fatal": {}, "nonfatal": {}}]
    v = pcie_aer_trend.classify(cards)
    assert v["verdict"] == "clean"


def test_classify_any_fatal_critical():
    cards = [{"gpu_bdf": "0000:01:00.0",
              "correctable": {"TOTAL_ERR_COR": 0},
              "fatal": {"TLP": 1},
              "nonfatal": {}}]
    v = pcie_aer_trend.classify(cards)
    assert v["verdict"] == "any_fatal"
    assert "TLP" in v["reason"]


def test_classify_any_nonfatal():
    cards = [{"gpu_bdf": "0000:01:00.0",
              "correctable": {"TOTAL_ERR_COR": 0},
              "fatal": {},
              "nonfatal": {"DLP": 3}}]
    v = pcie_aer_trend.classify(cards)
    assert v["verdict"] == "any_nonfatal"


def test_classify_high_correctable():
    cards = [{"gpu_bdf": "0000:01:00.0",
              "correctable": {"TOTAL_ERR_COR": 500},
              "fatal": {}, "nonfatal": {}}]
    v = pcie_aer_trend.classify(cards)
    assert v["verdict"] == "high_correctable"


def test_classify_low_correctable():
    cards = [{"gpu_bdf": "0000:01:00.0",
              "correctable": {"TOTAL_ERR_COR": 5},
              "fatal": {}, "nonfatal": {}}]
    v = pcie_aer_trend.classify(cards)
    assert v["verdict"] == "low_correctable"


def test_classify_no_gpus():
    v = pcie_aer_trend.classify([])
    assert v["verdict"] == "no_gpus"


def test_classify_picks_worst_fatal_over_correctable():
    cards = [
        {"gpu_bdf": "0000:01:00.0",
         "correctable": {"TOTAL_ERR_COR": 1000},
         "fatal": {}, "nonfatal": {}},
        {"gpu_bdf": "0000:02:00.0",
         "correctable": {"TOTAL_ERR_COR": 0},
         "fatal": {"TLP": 1}, "nonfatal": {}},
    ]
    v = pcie_aer_trend.classify(cards)
    assert v["verdict"] == "any_fatal"


def test_classify_recipe_for_fatal_suggests_reseating():
    cards = [{"gpu_bdf": "0000:01:00.0",
              "correctable": {"TOTAL_ERR_COR": 0},
              "fatal": {"TLP": 1}, "nonfatal": {}}]
    v = pcie_aer_trend.classify(cards)
    assert ("reseat" in v["recommendation"].lower()
            or "riser" in v["recommendation"].lower()
            or "slot" in v["recommendation"].lower())


# --- status ----------------------------------------------------

def test_status_no_gpus(tmp_path, monkeypatch):
    monkeypatch.setattr(pcie_aer_trend, "_PCI_ROOT", str(tmp_path))
    monkeypatch.setattr(pcie_aer_trend, "baseline_path",
                          lambda: str(tmp_path / "baseline.json"))
    s = pcie_aer_trend.status()
    assert s["ok"] is True
    assert s["gpu_count"] == 0
    assert s["verdict"]["verdict"] == "no_gpus"


def test_status_clean_state(tmp_path, monkeypatch):
    # The live-rig case
    _mk_gpu(tmp_path, "0000:01:00.0", correctable=_SAMPLE_AER,
            fatal=_SAMPLE_AER.replace("TOTAL_ERR_COR", "Undefined"),
            nonfatal=_SAMPLE_AER.replace("TOTAL_ERR_COR", "Undefined"))
    bp = tmp_path / "baseline.json"
    monkeypatch.setattr(pcie_aer_trend, "_PCI_ROOT", str(tmp_path))
    monkeypatch.setattr(pcie_aer_trend, "baseline_path", lambda: str(bp))
    s = pcie_aer_trend.status()
    assert s["gpu_count"] == 1
    assert s["verdict"]["verdict"] == "clean"


def test_status_with_dirty_correctable(tmp_path, monkeypatch):
    _mk_gpu(tmp_path, "0000:01:00.0", correctable=_DIRTY_AER)
    bp = tmp_path / "baseline.json"
    monkeypatch.setattr(pcie_aer_trend, "_PCI_ROOT", str(tmp_path))
    monkeypatch.setattr(pcie_aer_trend, "baseline_path", lambda: str(bp))
    s = pcie_aer_trend.status()
    card = s["cards"][0]
    assert card["correctable"]["TOTAL_ERR_COR"] == 8
    assert s["verdict"]["verdict"] == "low_correctable"


def test_status_persists_baseline_and_computes_delta(tmp_path, monkeypatch):
    _mk_gpu(tmp_path, "0000:01:00.0", correctable=_SAMPLE_AER)
    bp = tmp_path / "baseline.json"
    monkeypatch.setattr(pcie_aer_trend, "_PCI_ROOT", str(tmp_path))
    monkeypatch.setattr(pcie_aer_trend, "baseline_path", lambda: str(bp))
    s1 = pcie_aer_trend.status()
    assert s1["drift"]["status"] == "baseline_recorded"
    # Update file → new errors appear
    (tmp_path / "0000:01:00.0" / "aer_dev_correctable").write_text(_DIRTY_AER)
    s2 = pcie_aer_trend.status()
    assert s2["drift"]["status"] == "drift_detected"
    # Delta picks up the new counts
    assert s2["drift"]["deltas"]["0000:01:00.0"]["TOTAL_ERR_COR"] == 8


def test_status_no_drift_on_repeat(tmp_path, monkeypatch):
    _mk_gpu(tmp_path, "0000:01:00.0", correctable=_SAMPLE_AER)
    bp = tmp_path / "baseline.json"
    monkeypatch.setattr(pcie_aer_trend, "_PCI_ROOT", str(tmp_path))
    monkeypatch.setattr(pcie_aer_trend, "baseline_path", lambda: str(bp))
    pcie_aer_trend.status()
    s2 = pcie_aer_trend.status()
    assert s2["drift"]["status"] == "no_drift"
