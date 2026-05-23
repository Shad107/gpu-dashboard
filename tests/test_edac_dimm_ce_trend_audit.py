"""Tests for modules/edac_dimm_ce_trend_audit.py — R&D #71.4."""
from __future__ import annotations

import pytest

from gpu_dashboard.modules import edac_dimm_ce_trend_audit as mod


def _mk_dimm(root, mc, dimm, *, label="A0", size_mb=16384,
                  ce_count=0, ue_count=0):
    d = root / mc / dimm
    d.mkdir(parents=True, exist_ok=True)
    (d / "dimm_label").write_text(label + "\n")
    (d / "size_mb").write_text(f"{size_mb}\n")
    (d / "dimm_ce_count").write_text(f"{ce_count}\n")
    (d / "dimm_ue_count").write_text(f"{ue_count}\n")


# --- list_dimms ------------------------------------------------

def test_list_dimms_missing(tmp_path):
    assert mod.list_dimms(str(tmp_path / "nope")) == []


def test_list_dimms_two(tmp_path):
    _mk_dimm(tmp_path, "mc0", "dimm0", label="A0")
    _mk_dimm(tmp_path, "mc0", "dimm1", label="A1", ce_count=5)
    out = mod.list_dimms(str(tmp_path))
    assert len(out) == 2
    by_label = {d["label"]: d for d in out}
    assert by_label["A1"]["ce_count"] == 5


def test_list_dimms_skips_non_mc(tmp_path):
    _mk_dimm(tmp_path, "mc0", "dimm0", label="A0")
    (tmp_path / "subsystem").mkdir()
    out = mod.list_dimms(str(tmp_path))
    assert len(out) == 1
    assert out[0]["mc"] == "mc0"


# --- classify ---------------------------------------------------

def test_classify_unknown():
    v = mod.classify([], False, 0)
    assert v["verdict"] == "unknown"


def test_classify_edac_unsupported():
    v = mod.classify([], True, 0)
    assert v["verdict"] == "edac_unsupported"


def test_classify_ue():
    v = mod.classify(
        [{"mc": "mc0", "dimm": "dimm0", "label": "A0",
            "size_mb": 16384, "ce_count": 0,
            "ue_count": 1}],
        True, 1)
    assert v["verdict"] == "dimm_ue_present"


def test_classify_ce_rising():
    v = mod.classify(
        [{"mc": "mc0", "dimm": "dimm0", "label": "A0",
            "size_mb": 16384, "ce_count": 5000,
            "ue_count": 0}],
        True, 1)
    assert v["verdict"] == "dimm_ce_rising"


def test_classify_ce_steady():
    v = mod.classify(
        [{"mc": "mc0", "dimm": "dimm0", "label": "A0",
            "size_mb": 16384, "ce_count": 5,
            "ue_count": 0}],
        True, 1)
    assert v["verdict"] == "dimm_ce_nonzero_steady"


def test_classify_ok():
    v = mod.classify(
        [{"mc": "mc0", "dimm": "dimm0", "label": "A0",
            "size_mb": 16384, "ce_count": 0,
            "ue_count": 0}],
        True, 1)
    assert v["verdict"] == "ok"


# Priority : ue > rising > steady
def test_priority_ue_over_rising():
    v = mod.classify(
        [{"mc": "mc0", "dimm": "dimm0", "label": "A0",
            "size_mb": 16384, "ce_count": 5000,
            "ue_count": 1}],
        True, 1)
    assert v["verdict"] == "dimm_ue_present"


def test_priority_rising_over_steady():
    v = mod.classify(
        [{"mc": "mc0", "dimm": "dimm0", "label": "A0",
            "size_mb": 16384, "ce_count": 2000,
            "ue_count": 0},
          {"mc": "mc0", "dimm": "dimm1", "label": "A1",
            "size_mb": 16384, "ce_count": 5,
            "ue_count": 0}],
        True, 1)
    assert v["verdict"] == "dimm_ce_rising"


# --- status integration -----------------------------------------

def test_status_unknown(tmp_path):
    out = mod.status(None, str(tmp_path / "nope"))
    assert out["ok"] is False
    assert out["verdict"]["verdict"] == "unknown"


def test_status_unsupported(tmp_path):
    # edac/mc dir exists but no mc<N> entries
    (tmp_path / "subsystem").mkdir()
    out = mod.status(None, str(tmp_path))
    assert out["ok"] is True
    assert out["verdict"]["verdict"] == "edac_unsupported"


def test_status_ok_synthetic(tmp_path):
    _mk_dimm(tmp_path, "mc0", "dimm0", label="A0")
    _mk_dimm(tmp_path, "mc0", "dimm1", label="A1")
    out = mod.status(None, str(tmp_path))
    assert out["ok"] is True
    assert out["dimm_count"] == 2
    assert out["mc_count"] == 1
    assert out["verdict"]["verdict"] == "ok"


def test_status_ue_synthetic(tmp_path):
    _mk_dimm(tmp_path, "mc0", "dimm0", label="A0",
                  ue_count=1)
    out = mod.status(None, str(tmp_path))
    assert out["verdict"]["verdict"] == "dimm_ue_present"
