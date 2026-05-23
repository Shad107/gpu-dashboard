"""Tests for modules/power_supply_audit.py — R&D #51.1."""
from __future__ import annotations

import pytest

from gpu_dashboard.modules import power_supply_audit as mod


def _mk_supply(root, name, type_="Battery", **fields):
    d = root / name
    d.mkdir(parents=True, exist_ok=True)
    (d / "type").write_text(type_ + "\n")
    for k, v in fields.items():
        (d / k).write_text(str(v) + "\n")


# --- list_supplies ----------------------------------------------

def test_list_supplies_missing(tmp_path):
    assert mod.list_supplies(str(tmp_path / "nope")) == []


def test_list_supplies_empty(tmp_path):
    assert mod.list_supplies(str(tmp_path)) == []


def test_list_supplies_battery(tmp_path):
    _mk_supply(tmp_path, "BAT0", type_="Battery", capacity=78,
                cycle_count=234, charge_full=42000000,
                charge_full_design=48000000)
    out = mod.list_supplies(str(tmp_path))
    assert len(out) == 1
    assert out[0]["type"] == "Battery"
    assert out[0]["capacity"] == 78
    assert out[0]["charge_full"] == 42000000


def test_list_supplies_ac(tmp_path):
    _mk_supply(tmp_path, "AC", type_="Mains", online=1)
    out = mod.list_supplies(str(tmp_path))
    assert out[0]["online"] == 1


# --- classify ---------------------------------------------------

def _battery(name="BAT0", capacity=78, charge_full=42000000,
              charge_full_design=48000000, charge_end=100,
              cycle_count=200):
    return {"name": name, "type": "Battery", "status": "Discharging",
              "capacity": capacity, "cycle_count": cycle_count,
              "charge_full": charge_full,
              "charge_full_design": charge_full_design,
              "charge_control_end_threshold": charge_end}


def _mains(name="AC", online=1):
    return {"name": name, "type": "Mains", "online": online}


def test_classify_no_power_supply():
    v = mod.classify([])
    assert v["verdict"] == "no_power_supply"


def test_classify_ok():
    v = mod.classify([_battery(charge_end=80), _mains(online=1)])
    assert v["verdict"] == "ok"


def test_classify_battery_degraded():
    # 20M / 48M = 41 % < 60 %
    v = mod.classify([_battery(charge_full=20000000)])
    assert v["verdict"] == "battery_degraded"
    assert "42%" in v["reason"]


def test_classify_no_ac():
    v = mod.classify([_battery(charge_end=80), _mains(online=0)])
    assert v["verdict"] == "no_ac"


def test_classify_charge_threshold_unset():
    v = mod.classify([_battery(charge_end=100), _mains(online=1)])
    assert v["verdict"] == "charge_threshold_unset"


def test_classify_priority_degraded_wins_over_no_ac():
    v = mod.classify([_battery(charge_full=20000000,
                                  charge_end=100),
                       _mains(online=0)])
    assert v["verdict"] == "battery_degraded"


# --- status integration -----------------------------------------

def test_status_empty(monkeypatch, tmp_path):
    monkeypatch.setattr(mod, "_SYS_POWER", str(tmp_path / "nope"))
    out = mod.status()
    assert out["ok"] is False
    assert out["verdict"]["verdict"] == "no_power_supply"


def test_status_with_supplies(monkeypatch, tmp_path):
    sysp = tmp_path / "p"
    _mk_supply(sysp, "BAT0", type_="Battery", capacity=78,
                charge_full=42000000, charge_full_design=48000000,
                charge_control_end_threshold=80)
    _mk_supply(sysp, "AC", type_="Mains", online=1)
    monkeypatch.setattr(mod, "_SYS_POWER", str(sysp))
    out = mod.status()
    assert out["ok"] is True
    assert out["supply_count"] == 2
    assert out["verdict"]["verdict"] == "ok"
