"""Tests for modules/rfkill_bluetooth_audit.py — R&D #63.1."""
from __future__ import annotations

import pytest

from gpu_dashboard.modules import rfkill_bluetooth_audit as mod


def _mk_rfkill(root, idx, *, name="phy0", type_="wlan",
                 state=1, soft=0, hard=0, persistent=1):
    d = root / f"rfkill{idx}"
    d.mkdir(parents=True, exist_ok=True)
    (d / "name").write_text(name + "\n")
    (d / "type").write_text(type_ + "\n")
    (d / "state").write_text(f"{state}\n")
    (d / "soft").write_text(f"{soft}\n")
    (d / "hard").write_text(f"{hard}\n")
    (d / "persistent").write_text(f"{persistent}\n")


def _mk_hci(root, idx, *, address="00:11:22:33:44:55",
              type_="primary", power_control="on"):
    d = root / f"hci{idx}"
    d.mkdir(parents=True, exist_ok=True)
    (d / "address").write_text(address + "\n")
    (d / "type").write_text(type_ + "\n")
    pwr = d / "power"
    pwr.mkdir(parents=True, exist_ok=True)
    (pwr / "control").write_text(power_control + "\n")


# --- list_rfkills + list_bluetooth ------------------------------

def test_list_rfkills_missing(tmp_path):
    assert mod.list_rfkills(str(tmp_path / "nope")) == []


def test_list_rfkills(tmp_path):
    _mk_rfkill(tmp_path, 0, name="phy0", type_="wlan", hard=1)
    _mk_rfkill(tmp_path, 1, name="hci0", type_="bluetooth")
    (tmp_path / "other").mkdir()
    out = mod.list_rfkills(str(tmp_path))
    assert len(out) == 2


def test_list_bluetooth_missing(tmp_path):
    assert mod.list_bluetooth(str(tmp_path / "nope")) == []


def test_list_bluetooth(tmp_path):
    _mk_hci(tmp_path, 0)
    out = mod.list_bluetooth(str(tmp_path))
    assert len(out) == 1
    assert out[0]["power_control"] == "on"


# --- classify ---------------------------------------------------

def _r(id_="rfkill0", name="phy0", type_="wlan", soft=0, hard=0,
        persistent=1):
    return {"id": id_, "name": name, "type": type_, "state": 1,
              "soft": soft, "hard": hard, "persistent": persistent}


def _b(id_="hci0", power_control="on"):
    return {"id": id_, "address": "00:11:22:33:44:55",
              "type": "primary",
              "power_control": power_control}


def test_classify_unknown():
    v = mod.classify([], [])
    assert v["verdict"] == "unknown"


def test_classify_ok():
    v = mod.classify([_r()], [_b()])
    assert v["verdict"] == "ok"


def test_classify_hw_kill():
    v = mod.classify([_r(hard=1)], [_b()])
    assert v["verdict"] == "hw_kill_blocks"


def test_classify_soft_stuck():
    v = mod.classify([_r(soft=1, persistent=0)], [_b()])
    assert v["verdict"] == "soft_block_stuck"


def test_classify_soft_persistent_not_stuck():
    # persistent=1 → not stuck
    v = mod.classify([_r(soft=1, persistent=1)], [_b()])
    assert v["verdict"] == "ok"


def test_classify_bt_autosuspend():
    v = mod.classify([_r()], [_b(power_control="auto")])
    assert v["verdict"] == "bt_autosuspend_churn"


def test_classify_priority_hw_kill_wins():
    v = mod.classify(
        [_r(hard=1, soft=1, persistent=0)],
        [_b(power_control="auto")])
    assert v["verdict"] == "hw_kill_blocks"


# --- status integration -----------------------------------------

def test_status_unknown(tmp_path):
    out = mod.status(None, str(tmp_path / "no_rfkill"),
                       str(tmp_path / "no_bt"))
    assert out["ok"] is False
    assert out["verdict"]["verdict"] == "unknown"


def test_status_live_like(tmp_path):
    rf = tmp_path / "rfkill"
    _mk_rfkill(rf, 0, name="phy0", type_="wlan",
                  soft=1, persistent=0)
    bt = tmp_path / "bluetooth"
    _mk_hci(bt, 0, power_control="on")
    out = mod.status(None, str(rf), str(bt))
    assert out["ok"] is True
    assert out["rfkill_count"] == 1
    assert out["bluetooth_count"] == 1
    assert out["verdict"]["verdict"] == "soft_block_stuck"
