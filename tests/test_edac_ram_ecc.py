"""Tests for modules/edac_ram_ecc.py — R&D #41.2."""
from __future__ import annotations

from pathlib import Path

import pytest

from gpu_dashboard.modules import edac_ram_ecc as mod


def _mk_controller(root: Path, mc: str, *, mc_name: str = "amd64_edac_mod",
                     ce: int = 0, ue: int = 0, size_mb: int = 32768,
                     dimms: list | None = None):
    mcdir = root / mc
    mcdir.mkdir(parents=True, exist_ok=True)
    (mcdir / "mc_name").write_text(mc_name + "\n")
    (mcdir / "ce_count").write_text(str(ce) + "\n")
    (mcdir / "ue_count").write_text(str(ue) + "\n")
    (mcdir / "size_mb").write_text(str(size_mb) + "\n")
    for d in dimms or []:
        ddir = mcdir / d["name"]
        ddir.mkdir(parents=True, exist_ok=True)
        (ddir / "dimm_label").write_text(d.get("label", "") + "\n")
        (ddir / "size_mb").write_text(str(d.get("size_mb", 16384)) + "\n")
        (ddir / "dimm_ce_count").write_text(str(d.get("ce", 0)) + "\n")
        (ddir / "dimm_ue_count").write_text(str(d.get("ue", 0)) + "\n")


# --- list_controllers ----------------------------------------------

def test_list_controllers_empty(tmp_path):
    assert mod.list_controllers(str(tmp_path / "nope")) == []


def test_list_controllers_finds_mcs(tmp_path):
    _mk_controller(tmp_path, "mc0")
    _mk_controller(tmp_path, "mc1")
    (tmp_path / "power").mkdir()  # decoy non-mc dir
    assert mod.list_controllers(str(tmp_path)) == ["mc0", "mc1"]


# --- read_controller ----------------------------------------------

def test_read_controller_no_dimms(tmp_path):
    _mk_controller(tmp_path, "mc0", ce=3, ue=0)
    c = mod.read_controller(str(tmp_path), "mc0")
    assert c["name"] == "mc0"
    assert c["driver"] == "amd64_edac_mod"
    assert c["ce_count"] == 3
    assert c["ue_count"] == 0
    assert c["dimms"] == []


def test_read_controller_with_dimms(tmp_path):
    _mk_controller(tmp_path, "mc0", ce=5, ue=1, dimms=[
        {"name": "dimm0", "label": "CPU0_A1", "size_mb": 32768,
         "ce": 5, "ue": 0},
        {"name": "dimm1", "label": "CPU0_A2", "size_mb": 32768,
         "ce": 0, "ue": 1},
    ])
    c = mod.read_controller(str(tmp_path), "mc0")
    assert len(c["dimms"]) == 2
    assert c["dimms"][0]["label"] == "CPU0_A1"
    assert c["dimms"][0]["ce_count"] == 5
    assert c["dimms"][1]["ue_count"] == 1


def test_read_controller_missing_files(tmp_path):
    # Create mc0 with NO counter files — should default counts to 0
    (tmp_path / "mc0").mkdir()
    c = mod.read_controller(str(tmp_path), "mc0")
    assert c["ce_count"] == 0
    assert c["ue_count"] == 0


# --- classify ------------------------------------------------------

def _ctrl(name="mc0", ce=0, ue=0, dimms=None):
    return {"name": name, "driver": "amd64_edac_mod",
              "ce_count": ce, "ue_count": ue, "size_mb": 32768,
              "dimms": dimms or []}


def _dimm(name="dimm0", label="CPU0_A1", ce=0, ue=0):
    return {"name": name, "label": label, "size_mb": 16384,
              "ce_count": ce, "ue_count": ue, "mem_type": None,
              "dev_type": None}


def test_classify_ecc_disabled_when_no_controllers():
    v = mod.classify([])
    assert v["verdict"] == "ecc_disabled"
    assert "modprobe" in v["recommendation"]


def test_classify_ecc_clean():
    v = mod.classify([_ctrl(ce=0, ue=0)])
    assert v["verdict"] == "ecc_clean"
    assert v["recommendation"] == ""


def test_classify_ce_climbing():
    v = mod.classify([
        _ctrl(ce=12, ue=0, dimms=[_dimm("dimm0", "CPU0_A1", ce=12)])
    ])
    assert v["verdict"] == "ce_climbing"
    assert "12" in v["reason"]
    assert "CPU0_A1" in v["reason"]
    assert "rasdaemon" in v["recommendation"]


def test_classify_ue_present_wins_over_ce():
    # CE + UE both present — UE takes priority (critical).
    v = mod.classify([
        _ctrl(ce=5, ue=1,
               dimms=[_dimm("dimm0", "CPU0_A1", ce=5),
                       _dimm("dimm1", "CPU0_A2", ue=1)])
    ])
    assert v["verdict"] == "ue_present"
    assert "CPU0_A2" in v["reason"]
    assert "replace" in v["recommendation"].lower()


def test_classify_aggregates_across_controllers():
    v = mod.classify([
        _ctrl(name="mc0", ce=3, ue=0),
        _ctrl(name="mc1", ce=4, ue=0),
    ])
    assert v["verdict"] == "ce_climbing"
    assert "7" in v["reason"]  # 3+4


# --- status integration -------------------------------------------

def test_status_no_edac(monkeypatch, tmp_path):
    monkeypatch.setattr(mod, "_SYS_EDAC_MC", str(tmp_path / "nope"))
    out = mod.status()
    assert out["ok"] is False
    assert out["verdict"]["verdict"] == "unknown"


def test_status_empty_edac_dir(monkeypatch, tmp_path):
    # /sys/devices/system/edac/mc exists but no mc* dirs → disabled
    edac = tmp_path / "edac"
    edac.mkdir()
    monkeypatch.setattr(mod, "_SYS_EDAC_MC", str(edac))
    out = mod.status()
    assert out["ok"] is True
    assert out["verdict"]["verdict"] == "ecc_disabled"


def test_status_with_controllers(monkeypatch, tmp_path):
    edac = tmp_path / "edac"
    edac.mkdir()
    _mk_controller(edac, "mc0", ce=2, ue=0, dimms=[
        {"name": "dimm0", "label": "CPU0_A1", "size_mb": 32768,
         "ce": 2, "ue": 0},
    ])
    monkeypatch.setattr(mod, "_SYS_EDAC_MC", str(edac))
    out = mod.status()
    assert out["ok"] is True
    assert out["controller_count"] == 1
    assert out["ce_total"] == 2
    assert out["ue_total"] == 0
    assert out["verdict"]["verdict"] == "ce_climbing"
