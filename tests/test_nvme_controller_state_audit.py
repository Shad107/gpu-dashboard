"""Tests for modules/nvme_controller_state_audit.py — R&D #86.3."""
from __future__ import annotations

import pytest

from gpu_dashboard.modules import nvme_controller_state_audit as mod


def _mk_ctrl(tmp_path, name, *, state="live",
              cntrltype="io", firmware_rev="1.0",
              numa_node=0, transport="pcie",
              model="Samsung 990 PRO"):
    d = tmp_path / name
    d.mkdir(parents=True, exist_ok=True)
    (d / "state").write_text(state + "\n")
    (d / "cntrltype").write_text(cntrltype + "\n")
    (d / "firmware_rev").write_text(firmware_rev + "\n")
    (d / "numa_node").write_text(f"{numa_node}\n")
    (d / "transport").write_text(transport + "\n")
    (d / "model").write_text(model + "\n")


# --- list_controllers ------------------------------------------

def test_list_missing(tmp_path):
    assert mod.list_controllers(
        str(tmp_path / "nope")) == []


def test_list_basic(tmp_path):
    _mk_ctrl(tmp_path, "nvme0")
    _mk_ctrl(tmp_path, "nvme1")
    (tmp_path / "nvme-subsys0").mkdir()  # not nvmeN
    out = mod.list_controllers(str(tmp_path))
    assert out == ["nvme0", "nvme1"]


# --- read_controller -------------------------------------------

def test_read_controller(tmp_path):
    _mk_ctrl(tmp_path, "nvme0", state="resetting",
              firmware_rev="3B2QJXD7", numa_node=0,
              model="Samsung SSD 990 PRO 2TB")
    out = mod.read_controller(str(tmp_path), "nvme0")
    assert out["state"] == "resetting"
    assert out["firmware_rev"] == "3B2QJXD7"
    assert out["model"] == "Samsung SSD 990 PRO 2TB"


# --- _detect_firmware_mismatch ---------------------------------

def test_no_mismatch():
    ctrls = [
        {"model": "Samsung 990", "firmware_rev": "A"},
        {"model": "Samsung 990", "firmware_rev": "A"},
    ]
    assert mod._detect_firmware_mismatch(ctrls) is None


def test_mismatch_same_model():
    ctrls = [
        {"model": "Samsung 990", "firmware_rev": "A"},
        {"model": "Samsung 990", "firmware_rev": "B"},
    ]
    out = mod._detect_firmware_mismatch(ctrls)
    assert out is not None
    assert out["model"] == "Samsung 990"
    assert out["firmware_revs"] == ["A", "B"]


def test_different_models_no_mismatch():
    ctrls = [
        {"model": "Samsung 990", "firmware_rev": "A"},
        {"model": "WD SN850", "firmware_rev": "B"},
    ]
    assert mod._detect_firmware_mismatch(ctrls) is None


# --- classify --------------------------------------------------

def test_classify_unknown():
    v = mod.classify([])
    assert v["verdict"] == "unknown"


def _ctrl(name="nvme0", state="live", firmware_rev="1.0",
           numa_node=0, transport="pcie",
           model="Generic"):
    return {"name": name, "state": state,
              "cntrltype": "io",
              "firmware_rev": firmware_rev,
              "numa_node": numa_node,
              "transport": transport, "model": model}


def test_classify_ok():
    v = mod.classify([_ctrl()])
    assert v["verdict"] == "ok"


def test_classify_dead():
    v = mod.classify([_ctrl(state="dead")])
    assert v["verdict"] == "controller_dead"


def test_classify_deleting():
    v = mod.classify([_ctrl(state="deleting")])
    assert v["verdict"] == "controller_dead"


def test_classify_resetting():
    v = mod.classify([_ctrl(state="resetting")])
    assert v["verdict"] == "controller_resetting"


def test_classify_connecting():
    v = mod.classify([_ctrl(state="connecting")])
    assert v["verdict"] == "controller_resetting"


def test_classify_firmware_mismatch():
    v = mod.classify([
        _ctrl(name="nvme0",
                firmware_rev="A", model="Samsung 990"),
        _ctrl(name="nvme1",
                firmware_rev="B", model="Samsung 990"),
    ])
    assert v["verdict"] == "firmware_mismatch_same_model"


def test_classify_numa_node_unset():
    v = mod.classify([_ctrl(numa_node=-1)])
    assert v["verdict"] == "numa_node_unset"


def test_classify_numa_unset_non_pcie_ok():
    # tcp/rdma transport — NUMA may legitimately be -1
    v = mod.classify([
        _ctrl(numa_node=-1, transport="tcp")])
    assert v["verdict"] == "ok"


# Priority : dead > resetting > mismatch > numa
def test_priority_dead_over_resetting():
    v = mod.classify([
        _ctrl(name="nvme0", state="dead"),
        _ctrl(name="nvme1", state="resetting"),
    ])
    assert v["verdict"] == "controller_dead"


def test_priority_resetting_over_mismatch():
    v = mod.classify([
        _ctrl(name="nvme0", state="resetting",
                firmware_rev="A", model="X"),
        _ctrl(name="nvme1", state="live",
                firmware_rev="B", model="X"),
    ])
    assert v["verdict"] == "controller_resetting"


def test_priority_mismatch_over_numa():
    v = mod.classify([
        _ctrl(name="nvme0", firmware_rev="A",
                model="X", numa_node=-1),
        _ctrl(name="nvme1", firmware_rev="B",
                model="X", numa_node=-1),
    ])
    assert v["verdict"] == "firmware_mismatch_same_model"


# --- status integration ----------------------------------------

def test_status_unknown(tmp_path):
    out = mod.status(None, str(tmp_path / "nope"))
    assert out["ok"] is False
    assert out["verdict"]["verdict"] == "unknown"


def test_status_ok_synthetic(tmp_path):
    _mk_ctrl(tmp_path, "nvme0", state="live",
              firmware_rev="3B2QJXD7", numa_node=0)
    out = mod.status(None, str(tmp_path))
    assert out["ok"] is True
    assert out["controller_count"] == 1
    assert out["verdict"]["verdict"] == "ok"


def test_status_dead_synthetic(tmp_path):
    _mk_ctrl(tmp_path, "nvme0", state="dead")
    out = mod.status(None, str(tmp_path))
    assert out["ok"] is False
    assert out["verdict"]["verdict"] == "controller_dead"
