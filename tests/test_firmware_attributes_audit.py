"""Tests for modules/firmware_attributes_audit.py — R&D #73.3."""
from __future__ import annotations

import pytest

from gpu_dashboard.modules import firmware_attributes_audit as mod


def _mk_attr(root, vendor, name, *, current_value="On",
                 default_value="On", type_="string"):
    d = root / vendor / "attributes" / name
    d.mkdir(parents=True, exist_ok=True)
    (d / "current_value").write_text(current_value + "\n")
    (d / "default_value").write_text(default_value + "\n")
    (d / "type").write_text(type_ + "\n")


def _mk_pending(root, vendor, value):
    d = root / vendor
    d.mkdir(parents=True, exist_ok=True)
    (d / "pending_reboot").write_text(f"{value}\n")


# --- list_vendors -----------------------------------------------

def test_list_vendors_missing(tmp_path):
    assert mod.list_vendors(str(tmp_path / "nope")) == []


def test_list_vendors(tmp_path):
    (tmp_path / "dell-wmi-sysman").mkdir()
    (tmp_path / "thinklmi").mkdir()
    out = mod.list_vendors(str(tmp_path))
    assert sorted(out) == ["dell-wmi-sysman", "thinklmi"]


# --- list_attributes -------------------------------------------

def test_list_attributes(tmp_path):
    _mk_attr(tmp_path, "thinklmi", "ThermalMode",
                  current_value="Quiet")
    _mk_attr(tmp_path, "thinklmi", "FnLock",
                  current_value="Off")
    out = mod.list_attributes(str(tmp_path), "thinklmi")
    assert len(out) == 2
    by_name = {a["name"]: a for a in out}
    assert by_name["ThermalMode"]["current_value"] == "Quiet"


# --- classify ---------------------------------------------------

def test_classify_unknown():
    v = mod.classify(False, [], [], [])
    assert v["verdict"] == "unknown"


def test_classify_ok():
    v = mod.classify(True, ["thinklmi"],
                          [{"vendor": "thinklmi",
                              "name": "FnLock",
                              "current_value": "Off",
                              "default_value": "Off",
                              "type": "string"}],
                          [{"vendor": "thinklmi", "value": 0}])
    assert v["verdict"] == "ok"


def test_classify_pending_reboot():
    v = mod.classify(True, ["thinklmi"], [],
                          [{"vendor": "thinklmi", "value": 1}])
    assert v["verdict"] == "pending_reboot_stuck"


def test_classify_thermal_mode_quiet():
    v = mod.classify(True, ["thinklmi"],
                          [{"vendor": "thinklmi",
                              "name": "ThermalMode",
                              "current_value": "Quiet",
                              "default_value": "Balanced",
                              "type": "string"}],
                          [{"vendor": "thinklmi", "value": 0}])
    assert v["verdict"] == "thermal_mode_quiet"


def test_classify_power_limit_unlocked_off():
    v = mod.classify(True, ["dell-wmi-sysman"],
                          [{"vendor": "dell-wmi-sysman",
                              "name": "PowerLimitUnlock",
                              "current_value": "Disabled",
                              "default_value": "Disabled",
                              "type": "string"}],
                          [{"vendor": "dell-wmi-sysman",
                              "value": 0}])
    assert v["verdict"] == "power_limit_unlocked_off"


def test_classify_attributes_absent():
    v = mod.classify(True, ["thinklmi"], [],
                          [{"vendor": "thinklmi", "value": 0}])
    assert v["verdict"] == "attributes_absent"


# Priority : pending_reboot > thermal > power_limit
def test_priority_pending_over_thermal():
    v = mod.classify(True, ["thinklmi"],
                          [{"vendor": "thinklmi",
                              "name": "ThermalMode",
                              "current_value": "Quiet",
                              "default_value": "Balanced",
                              "type": "string"}],
                          [{"vendor": "thinklmi", "value": 1}])
    assert v["verdict"] == "pending_reboot_stuck"


def test_priority_thermal_over_power():
    v = mod.classify(True, ["thinklmi"],
                          [{"vendor": "thinklmi",
                              "name": "ThermalMode",
                              "current_value": "Quiet",
                              "default_value": "Balanced",
                              "type": "string"},
                            {"vendor": "thinklmi",
                              "name": "PowerLimitUnlock",
                              "current_value": "Disabled",
                              "default_value": "Disabled",
                              "type": "string"}],
                          [{"vendor": "thinklmi", "value": 0}])
    assert v["verdict"] == "thermal_mode_quiet"


# --- status integration -----------------------------------------

def test_status_unknown(tmp_path):
    out = mod.status(None, str(tmp_path / "nope"))
    assert out["ok"] is False
    assert out["verdict"]["verdict"] == "unknown"


def test_status_ok_synthetic(tmp_path):
    _mk_attr(tmp_path, "thinklmi", "FnLock",
                  current_value="Off")
    _mk_pending(tmp_path, "thinklmi", 0)
    out = mod.status(None, str(tmp_path))
    assert out["ok"] is True
    assert out["vendor_count"] == 1
    assert out["attribute_count"] == 1
    assert out["verdict"]["verdict"] == "ok"


def test_status_thermal_quiet_synthetic(tmp_path):
    _mk_attr(tmp_path, "thinklmi", "ThermalMode",
                  current_value="Quiet")
    _mk_pending(tmp_path, "thinklmi", 0)
    out = mod.status(None, str(tmp_path))
    assert out["verdict"]["verdict"] == "thermal_mode_quiet"
