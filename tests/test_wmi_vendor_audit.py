"""Tests for modules/wmi_vendor_audit.py — R&D #49.3."""
from __future__ import annotations

import pytest

from gpu_dashboard.modules import wmi_vendor_audit as mod


# --- list_wmi_guids ----------------------------------------------

def test_list_wmi_guids_missing(tmp_path):
    assert mod.list_wmi_guids(str(tmp_path / "nope")) == []


def test_list_wmi_guids_basic(tmp_path):
    (tmp_path / "DEADBEEF-1234-5678").mkdir()
    (tmp_path / "ABCDEF12-3456-7890").mkdir()
    assert len(mod.list_wmi_guids(str(tmp_path))) == 2


# --- detect_vendor_drivers ---------------------------------------

def test_detect_vendor_drivers_missing(tmp_path):
    assert mod.detect_vendor_drivers(str(tmp_path / "nope")) == []


def test_detect_vendor_drivers_thinkpad(tmp_path):
    d = tmp_path / "thinkpad_acpi"
    d.mkdir()
    (d / "charge_control_start_threshold").write_text("75\n")
    (d / "charge_control_end_threshold").write_text("80\n")
    out = mod.detect_vendor_drivers(str(tmp_path))
    assert len(out) == 1
    assert out[0]["name"] == "thinkpad_acpi"
    assert out[0]["charge_control_start_threshold"] == 75


# --- classify ----------------------------------------------------

def test_classify_no_wmi():
    v = mod.classify([], [])
    assert v["verdict"] == "no_wmi"


def test_classify_ok():
    v = mod.classify(["DEADBEEF"], [])
    assert v["verdict"] == "ok"


def test_classify_vendor_driver_active():
    v = mod.classify([],
                       [{"name": "thinkpad_acpi",
                          "charge_control_start_threshold": 75,
                          "charge_control_end_threshold": 80}])
    assert v["verdict"] == "vendor_driver_active"


def test_classify_battery_threshold_unset():
    v = mod.classify([],
                       [{"name": "thinkpad_acpi",
                          "charge_control_start_threshold": 100,
                          "charge_control_end_threshold": 100}])
    assert v["verdict"] == "battery_threshold_unset"


def test_classify_priority_battery_wins():
    v = mod.classify(["DEADBEEF"],
                       [{"name": "asus-wmi",
                          "charge_control_start_threshold": 100,
                          "charge_control_end_threshold": 100}])
    assert v["verdict"] == "battery_threshold_unset"


# --- status integration ------------------------------------------

def test_status_no_wmi(monkeypatch, tmp_path):
    monkeypatch.setattr(mod, "_SYS_CLASS_WMI",
                        str(tmp_path / "nope"))
    monkeypatch.setattr(mod, "_SYS_PLATFORM",
                        str(tmp_path / "nope2"))
    out = mod.status()
    assert out["ok"] is True
    assert out["verdict"]["verdict"] == "no_wmi"


def test_status_with_vendor_driver(monkeypatch, tmp_path):
    wmi_dir = tmp_path / "wmi"
    wmi_dir.mkdir()
    platform = tmp_path / "platform"
    platform.mkdir()
    (platform / "asus-wmi").mkdir()
    (platform / "asus-wmi" / "charge_control_start_threshold").write_text("75\n")
    (platform / "asus-wmi" / "charge_control_end_threshold").write_text("80\n")
    monkeypatch.setattr(mod, "_SYS_CLASS_WMI", str(wmi_dir))
    monkeypatch.setattr(mod, "_SYS_PLATFORM", str(platform))
    out = mod.status()
    assert out["verdict"]["verdict"] == "vendor_driver_active"
