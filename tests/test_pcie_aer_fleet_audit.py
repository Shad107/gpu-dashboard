"""Tests for modules/pcie_aer_fleet_audit.py — R&D #77.3."""
from __future__ import annotations

import pytest

from gpu_dashboard.modules import pcie_aer_fleet_audit as mod


def _mk_aer_file(d, name, error_counts):
    """error_counts: dict {label: count}"""
    p = d / f"aer_dev_{name}"
    text = "\n".join(f"{k} {v}" for k, v in error_counts.items())
    p.write_text(text + "\n")


def _mk_pci_device(root, bdf, *, class_id="0x010802",
                       driver="nvme",
                       correctable=None, fatal=None,
                       nonfatal=None):
    d = root / bdf
    d.mkdir(parents=True, exist_ok=True)
    (d / "class").write_text(class_id + "\n")
    (d / "uevent").write_text(
        f"DRIVER={driver}\nPCI_CLASS={class_id[2:]}\n")
    if correctable is not None:
        _mk_aer_file(d, "correctable", correctable)
    if fatal is not None:
        _mk_aer_file(d, "fatal", fatal)
    if nonfatal is not None:
        _mk_aer_file(d, "nonfatal", nonfatal)


def _zero():
    return {"RxErr": 0, "BadTLP": 0, "BadDLLP": 0}


# --- _read_aer_sum ---------------------------------------------

def test_read_aer_sum_missing(tmp_path):
    assert mod._read_aer_sum(str(tmp_path / "nope")) is None


def test_read_aer_sum_zero(tmp_path):
    p = tmp_path / "aer"
    p.write_text("RxErr 0\nBadTLP 0\nBadDLLP 0\n")
    assert mod._read_aer_sum(str(p)) == 0


def test_read_aer_sum_nonzero(tmp_path):
    p = tmp_path / "aer"
    p.write_text("RxErr 5\nBadTLP 2\nBadDLLP 0\n")
    assert mod._read_aer_sum(str(p)) == 7


# --- _classify_device ------------------------------------------

def test_classify_nvme():
    assert mod._classify_device(0x010802, "nvme") == "nvme"


def test_classify_bridge():
    assert mod._classify_device(0x060400, "pcieport") == "bridge"


def test_classify_nic():
    assert mod._classify_device(0x020000, "e1000e") == "nic"


def test_classify_gpu():
    assert mod._classify_device(0x030000, "nvidia") == "gpu"


def test_classify_other_none():
    assert mod._classify_device(None, None) == "other"


# --- list_aer_devices ------------------------------------------

def test_list_missing(tmp_path):
    assert mod.list_aer_devices(str(tmp_path / "nope")) == []


def test_list_skips_non_aer(tmp_path):
    _mk_pci_device(tmp_path, "0000:00:00.0", class_id="0x060000",
                       driver="pcieport")  # no aer files
    assert mod.list_aer_devices(str(tmp_path)) == []


def test_list_basic(tmp_path):
    _mk_pci_device(tmp_path, "0000:00:1c.0",
                       class_id="0x060400",
                       driver="pcieport",
                       correctable=_zero(),
                       fatal={"Undefined": 0},
                       nonfatal={"DataLink": 0})
    _mk_pci_device(tmp_path, "0000:01:00.0",
                       class_id="0x010802",
                       driver="nvme",
                       correctable=_zero(),
                       fatal={"Undefined": 0},
                       nonfatal={"DataLink": 0})
    out = mod.list_aer_devices(str(tmp_path))
    assert len(out) == 2
    by_bdf = {d["bdf"]: d for d in out}
    assert by_bdf["0000:00:1c.0"]["kind"] == "bridge"
    assert by_bdf["0000:01:00.0"]["kind"] == "nvme"
    assert all(d["correctable"] == 0 and d["fatal"] == 0
                   for d in out)


# --- classify ---------------------------------------------------

def test_classify_unknown_no_dir():
    v = mod.classify(False, [])
    assert v["verdict"] == "unknown"


def test_classify_unknown_empty():
    v = mod.classify(True, [])
    assert v["verdict"] == "unknown"


def test_classify_ok():
    v = mod.classify(True, [
        {"bdf": "0000:01:00.0", "kind": "nvme",
            "correctable": 0, "fatal": 0, "nonfatal": 0,
            "class_id": 0x010802, "driver": "nvme"}])
    assert v["verdict"] == "ok"


def test_classify_fleet_fatal():
    v = mod.classify(True, [
        {"bdf": "0000:01:00.0", "kind": "nvme",
            "correctable": 0, "fatal": 1, "nonfatal": 0,
            "class_id": 0x010802, "driver": "nvme"}])
    assert v["verdict"] == "fleet_fatal"


def test_classify_fleet_nonfatal():
    v = mod.classify(True, [
        {"bdf": "0000:01:00.0", "kind": "nvme",
            "correctable": 0, "fatal": 0, "nonfatal": 1,
            "class_id": 0x010802, "driver": "nvme"}])
    assert v["verdict"] == "fleet_nonfatal"


def test_classify_bridge_correctable_storm():
    v = mod.classify(True, [
        {"bdf": "0000:00:1c.0", "kind": "bridge",
            "correctable": 500, "fatal": 0, "nonfatal": 0,
            "class_id": 0x060400, "driver": "pcieport"}])
    assert v["verdict"] == "bridge_correctable_storm"


def test_classify_nvme_correctable():
    v = mod.classify(True, [
        {"bdf": "0000:01:00.0", "kind": "nvme",
            "correctable": 5, "fatal": 0, "nonfatal": 0,
            "class_id": 0x010802, "driver": "nvme"}])
    assert v["verdict"] == "nvme_or_nic_correctable"


def test_classify_nic_correctable():
    v = mod.classify(True, [
        {"bdf": "0000:02:00.0", "kind": "nic",
            "correctable": 5, "fatal": 0, "nonfatal": 0,
            "class_id": 0x020000, "driver": "e1000e"}])
    assert v["verdict"] == "nvme_or_nic_correctable"


def test_classify_bridge_below_threshold_ok():
    # Bridge with 50 correctable < 100 threshold → ok
    v = mod.classify(True, [
        {"bdf": "0000:00:1c.0", "kind": "bridge",
            "correctable": 50, "fatal": 0, "nonfatal": 0,
            "class_id": 0x060400, "driver": "pcieport"}])
    assert v["verdict"] == "ok"


# Priority : fatal > nonfatal > bridge_storm > nvme_or_nic
def test_priority_fatal_over_nonfatal():
    v = mod.classify(True, [
        {"bdf": "x", "kind": "nvme",
            "correctable": 0, "fatal": 1, "nonfatal": 1,
            "class_id": 0, "driver": ""}])
    assert v["verdict"] == "fleet_fatal"


def test_priority_nonfatal_over_bridge():
    v = mod.classify(True, [
        {"bdf": "x", "kind": "bridge",
            "correctable": 500, "fatal": 0, "nonfatal": 1,
            "class_id": 0x060400, "driver": ""}])
    assert v["verdict"] == "fleet_nonfatal"


def test_priority_bridge_over_nvme():
    v = mod.classify(True, [
        {"bdf": "x", "kind": "bridge",
            "correctable": 500, "fatal": 0, "nonfatal": 0,
            "class_id": 0x060400, "driver": ""},
        {"bdf": "y", "kind": "nvme",
            "correctable": 5, "fatal": 0, "nonfatal": 0,
            "class_id": 0x010802, "driver": "nvme"}])
    assert v["verdict"] == "bridge_correctable_storm"


# --- status integration ----------------------------------------

def test_status_unknown(tmp_path):
    out = mod.status(None, str(tmp_path / "nope"))
    assert out["ok"] is False
    assert out["verdict"]["verdict"] == "unknown"


def test_status_ok_synthetic(tmp_path):
    _mk_pci_device(tmp_path, "0000:01:00.0",
                       class_id="0x010802", driver="nvme",
                       correctable=_zero(),
                       fatal={"Undefined": 0},
                       nonfatal={"DataLink": 0})
    out = mod.status(None, str(tmp_path))
    assert out["ok"] is True
    assert out["device_count"] == 1
    assert out["verdict"]["verdict"] == "ok"


def test_status_fatal_synthetic(tmp_path):
    _mk_pci_device(tmp_path, "0000:01:00.0",
                       class_id="0x010802", driver="nvme",
                       correctable=_zero(),
                       fatal={"Undefined": 3},
                       nonfatal={"DataLink": 0})
    out = mod.status(None, str(tmp_path))
    assert out["verdict"]["verdict"] == "fleet_fatal"
    assert out["totals"]["fatal"] == 3
