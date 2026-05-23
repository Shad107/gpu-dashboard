"""Tests for modules/mei_intel_me_audit.py — R&D #62.2."""
from __future__ import annotations

import pytest

from gpu_dashboard.modules import mei_intel_me_audit as mod


def _mk_mei(root, idx, *, fw_status="40000245 80100008 00000000 "
                                          "00006e63 00100018 00004000",
              fw_ver="0:16.50.10.2422",
              hbm_ver="2.3",
              dev_state="enabled",
              tx_queue_limit="50"):
    d = root / f"mei{idx}"
    d.mkdir(parents=True, exist_ok=True)
    (d / "fw_status").write_text(fw_status + "\n")
    (d / "fw_ver").write_text(fw_ver + "\n")
    (d / "hbm_ver").write_text(hbm_ver + "\n")
    (d / "dev_state").write_text(dev_state + "\n")
    (d / "tx_queue_limit").write_text(tx_queue_limit + "\n")


# --- _fw_status_indicates_recovery ------------------------------

def test_fw_status_recovery():
    # op_mode 5 in bits 16-19 of word 0
    # 5 << 16 = 0x50000 → "00050000" has op_mode 5
    assert mod._fw_status_indicates_recovery(
        "00050000 0 0 0 0 0") is True


def test_fw_status_normal():
    # op_mode 0 (normal) → "00000000 ..."
    assert mod._fw_status_indicates_recovery(
        "00000000 0 0 0 0 0") is False


def test_fw_status_empty():
    assert mod._fw_status_indicates_recovery(None) is False
    assert mod._fw_status_indicates_recovery("") is False


# --- _fw_status_has_error ---------------------------------------

def test_fw_status_has_error():
    # bit 23 = 1 << 23 = 0x800000
    assert mod._fw_status_has_error(
        "00800000 0 0 0 0 0") is True


def test_fw_status_no_error():
    assert mod._fw_status_has_error(
        "00000245 0 0 0 0 0") is False


# --- list_mei_devices -------------------------------------------

def test_list_mei_devices_missing(tmp_path):
    assert mod.list_mei_devices(str(tmp_path / "nope")) == []


def test_list_mei_devices(tmp_path):
    _mk_mei(tmp_path, 0)
    _mk_mei(tmp_path, 1, dev_state="disabled")
    (tmp_path / "other").mkdir()
    out = mod.list_mei_devices(str(tmp_path))
    assert len(out) == 2


# --- list_dev_nodes ---------------------------------------------

def test_list_dev_nodes(tmp_path):
    (tmp_path / "mei0").write_text("")
    (tmp_path / "mei1").write_text("")
    (tmp_path / "sda").write_text("")
    out = mod.list_dev_nodes(str(tmp_path))
    assert out == ["mei0", "mei1"]


# --- classify ---------------------------------------------------

def _dev(id_="mei0", fw_status="40000245 0 0 0 0 0",
          dev_state="enabled"):
    return {"id": id_, "fw_status": fw_status, "fw_ver": "16.50",
              "hbm_ver": "2.3", "dev_state": dev_state,
              "tx_queue_limit": "50"}


def test_classify_absent():
    v = mod.classify([], [])
    assert v["verdict"] == "absent"


def test_classify_dev_nodes_only():
    v = mod.classify([], ["mei0"])
    assert v["verdict"] == "unknown"


def test_classify_ok():
    v = mod.classify([_dev()], ["mei0"])
    assert v["verdict"] == "ok"


def test_classify_recovery():
    v = mod.classify([_dev(fw_status="00050000 0 0 0 0 0")],
                       ["mei0"])
    assert v["verdict"] == "me_recovery_mode"


def test_classify_disabled():
    v = mod.classify([_dev(dev_state="disabled")], ["mei0"])
    assert v["verdict"] == "me_disabled_but_present"


def test_classify_fw_error():
    v = mod.classify([_dev(fw_status="00800000 0 0 0 0 0")],
                       ["mei0"])
    assert v["verdict"] == "fw_status_error"


def test_classify_priority_recovery_wins():
    # recovery + disabled + error → recovery wins
    v = mod.classify(
        [_dev(fw_status="00850000 0 0 0 0 0",
                dev_state="disabled")], ["mei0"])
    assert v["verdict"] == "me_recovery_mode"


# --- status integration -----------------------------------------

def test_status_absent(tmp_path):
    out = mod.status(None, str(tmp_path / "nomei"),
                       str(tmp_path / "nodev"))
    assert out["ok"] is False
    assert out["verdict"]["verdict"] == "absent"


def test_status_live_like(tmp_path):
    sm = tmp_path / "mei"
    _mk_mei(sm, 0)
    dv = tmp_path / "dev"
    dv.mkdir()
    (dv / "mei0").write_text("")
    out = mod.status(None, str(sm), str(dv))
    assert out["ok"] is True
    assert out["device_count"] == 1
    assert out["verdict"]["verdict"] == "ok"
