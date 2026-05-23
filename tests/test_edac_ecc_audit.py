"""Tests for modules/edac_ecc_audit.py — R&D #55.1."""
from __future__ import annotations

import pytest

from gpu_dashboard.modules import edac_ecc_audit as mod


def _mk_controller(root, idx, *, ue=0, ce=0, name="ie31200_edac",
                     size_mb=32768, dimms=None):
    d = root / f"mc{idx}"
    d.mkdir(parents=True, exist_ok=True)
    (d / "ue_count").write_text(f"{ue}\n")
    (d / "ce_count").write_text(f"{ce}\n")
    (d / "mc_name").write_text(name + "\n")
    (d / "size_mb").write_text(f"{size_mb}\n")
    for i, dimm in enumerate(dimms or []):
        dd = d / f"dimm{i}"
        dd.mkdir(parents=True, exist_ok=True)
        (dd / "dimm_ue_count").write_text(f"{dimm.get('ue', 0)}\n")
        (dd / "dimm_ce_count").write_text(f"{dimm.get('ce', 0)}\n")
        (dd / "dimm_label").write_text(
            dimm.get("label", f"DIMM_{i}") + "\n")
        (dd / "dimm_location").write_text(
            dimm.get("location", f"channel{i}") + "\n")
        (dd / "size").write_text(f"{dimm.get('size_mb', 16384)}\n")
    return d


# --- list_controllers -------------------------------------------

def test_list_controllers_missing(tmp_path):
    assert mod.list_controllers(str(tmp_path / "nope")) == []


def test_list_controllers_empty(tmp_path):
    assert mod.list_controllers(str(tmp_path)) == []


def test_list_controllers_basic(tmp_path):
    _mk_controller(tmp_path, 0,
                       dimms=[{"label": "A1", "ce": 0},
                                {"label": "A2", "ce": 0}])
    out = mod.list_controllers(str(tmp_path))
    assert len(out) == 1
    assert out[0]["mc_name"] == "ie31200_edac"
    assert len(out[0]["dimms"]) == 2


def test_list_controllers_with_errors(tmp_path):
    _mk_controller(tmp_path, 0, ce=5,
                       dimms=[{"label": "A1", "ce": 5}])
    out = mod.list_controllers(str(tmp_path))
    assert out[0]["ce_count"] == 5
    assert out[0]["dimms"][0]["ce_count"] == 5


# --- classify ---------------------------------------------------

def _ctrl(idx=0, ue=0, ce=0, dimms=None):
    if dimms is None:
        dimms = [{"id": "dimm0", "label": "A1", "ue_count": 0,
                    "ce_count": 0, "location": "channel0",
                    "size": 16384}]
    return {"id": f"mc{idx}", "ue_count": ue, "ce_count": ce,
              "mc_name": "test_edac", "size_mb": 32768,
              "dimms": dimms}


def test_classify_edac_absent():
    v = mod.classify([], edac_present=False)
    assert v["verdict"] == "edac_absent"


def test_classify_driver_missing():
    v = mod.classify([], edac_present=True)
    assert v["verdict"] == "driver_missing"


def test_classify_ok():
    v = mod.classify([_ctrl()], edac_present=True)
    assert v["verdict"] == "ok"


def test_classify_ue_on_controller():
    v = mod.classify([_ctrl(ue=3)], edac_present=True)
    assert v["verdict"] == "ue_present"


def test_classify_ue_on_dimm():
    dimms = [{"id": "dimm0", "label": "A1", "ue_count": 2,
                "ce_count": 0, "location": "ch0", "size": 16384}]
    v = mod.classify([_ctrl(dimms=dimms)], edac_present=True)
    assert v["verdict"] == "ue_present"
    assert "A1" in v["reason"]


def test_classify_ce_rising():
    dimms = [{"id": "dimm0", "label": "A1", "ue_count": 0,
                "ce_count": 12, "location": "ch0", "size": 16384}]
    v = mod.classify([_ctrl(dimms=dimms)], edac_present=True)
    assert v["verdict"] == "ce_rising"
    assert "A1" in v["reason"]


def test_classify_priority_ue_wins():
    dimms = [{"id": "dimm0", "label": "A1", "ue_count": 1,
                "ce_count": 99, "location": "ch0", "size": 16384}]
    v = mod.classify([_ctrl(dimms=dimms)], edac_present=True)
    assert v["verdict"] == "ue_present"


# --- status integration -----------------------------------------

def test_status_absent(tmp_path):
    out = mod.status(None, str(tmp_path / "nope"))
    assert out["ok"] is False
    assert out["verdict"]["verdict"] == "edac_absent"


def test_status_driver_missing(tmp_path):
    # Dir exists but no mc<N> children
    (tmp_path / "mc").mkdir()
    out = mod.status(None, str(tmp_path / "mc"))
    assert out["ok"] is True
    assert out["verdict"]["verdict"] == "driver_missing"


def test_status_with_controller(tmp_path):
    sysd = tmp_path / "mc"
    _mk_controller(sysd, 0,
                       dimms=[{"label": "A1", "ce": 7},
                                {"label": "A2", "ce": 0}])
    out = mod.status(None, str(sysd))
    assert out["ok"] is True
    assert out["controller_count"] == 1
    assert out["verdict"]["verdict"] == "ce_rising"
