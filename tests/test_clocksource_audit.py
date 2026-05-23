"""Tests for modules/clocksource_audit.py — R&D #33.4 clocksource audit."""
from __future__ import annotations

from pathlib import Path

import pytest

from gpu_dashboard.modules import clocksource_audit


def _mk_clocksource(root: Path, *, current: str = "tsc",
                       available: str = "tsc hpet acpi_pm"):
    root.mkdir(parents=True, exist_ok=True)
    (root / "current_clocksource").write_text(current + "\n")
    (root / "available_clocksource").write_text(available + "\n")


# --- field readers --------------------------------------------------

def test_read_current_returns_string(tmp_path):
    _mk_clocksource(tmp_path, current="kvm-clock")
    assert clocksource_audit.read_current(str(tmp_path)) == "kvm-clock"


def test_read_current_strips_whitespace(tmp_path):
    _mk_clocksource(tmp_path, current="  tsc \n")
    assert clocksource_audit.read_current(str(tmp_path)) == "tsc"


def test_read_current_missing_returns_none(tmp_path):
    assert clocksource_audit.read_current(str(tmp_path / "absent")) is None


def test_read_available_returns_list(tmp_path):
    _mk_clocksource(tmp_path, available="kvm-clock tsc hpet acpi_pm")
    av = clocksource_audit.read_available(str(tmp_path))
    assert av == ["kvm-clock", "tsc", "hpet", "acpi_pm"]


def test_read_available_missing_returns_empty(tmp_path):
    assert clocksource_audit.read_available(str(tmp_path / "absent")) == []


# --- virt detection ------------------------------------------------

def test_detect_virt_kvm_when_kvm_clock_available():
    assert clocksource_audit.detect_virt(["kvm-clock", "tsc", "hpet"]) == "kvm"


def test_detect_virt_xen_when_xen_available():
    assert clocksource_audit.detect_virt(["xen", "tsc", "hpet"]) == "xen"


def test_detect_virt_hyperv_when_hyperv():
    assert clocksource_audit.detect_virt(["hyperv_clocksource_tsc_page",
                                             "tsc", "hpet"]) == "hyperv"


def test_detect_virt_bare_metal_when_no_hint():
    assert clocksource_audit.detect_virt(["tsc", "hpet", "acpi_pm"]) is None


def test_detect_virt_empty():
    assert clocksource_audit.detect_virt([]) is None


# --- classify ------------------------------------------------------

def test_classify_tsc_on_bare_metal_is_optimal():
    v = clocksource_audit.classify(current="tsc",
                                       available=["tsc", "hpet", "acpi_pm"],
                                       virt=None)
    assert v["verdict"] == "optimal"
    assert v["recommendation"] == ""


def test_classify_kvm_clock_on_kvm_is_optimal():
    v = clocksource_audit.classify(current="kvm-clock",
                                       available=["kvm-clock", "tsc", "hpet"],
                                       virt="kvm")
    assert v["verdict"] == "optimal"


def test_classify_xen_on_xen_is_optimal():
    v = clocksource_audit.classify(current="xen",
                                       available=["xen", "tsc"],
                                       virt="xen")
    assert v["verdict"] == "optimal"


def test_classify_hpet_active_is_warn():
    v = clocksource_audit.classify(current="hpet",
                                       available=["tsc", "hpet", "acpi_pm"],
                                       virt=None)
    assert v["verdict"] == "hpet_active"
    assert "tsc" in v["recommendation"].lower()
    assert "clocksource=" in v["recommendation"]


def test_classify_jiffies_is_critical():
    v = clocksource_audit.classify(current="jiffies",
                                       available=["jiffies", "tsc"],
                                       virt=None)
    assert v["verdict"] == "low_res"


def test_classify_acpi_pm_is_acceptable():
    v = clocksource_audit.classify(current="acpi_pm",
                                       available=["acpi_pm", "tsc", "hpet"],
                                       virt=None)
    assert v["verdict"] == "acceptable"


def test_classify_tsc_on_kvm_is_suboptimal():
    # On a KVM guest, kvm-clock beats raw TSC for stability across vCPU
    # migrations / live migrate.
    v = clocksource_audit.classify(current="tsc",
                                       available=["kvm-clock", "tsc", "hpet"],
                                       virt="kvm")
    assert v["verdict"] == "suboptimal_virt"
    assert "kvm-clock" in v["recommendation"]


def test_classify_unknown_when_no_current():
    v = clocksource_audit.classify(current=None, available=[], virt=None)
    assert v["verdict"] == "unknown"


# --- status -------------------------------------------------------

def test_status_kvm_guest_with_kvm_clock(tmp_path, monkeypatch):
    # The live-rig case
    _mk_clocksource(tmp_path, current="kvm-clock",
                    available="kvm-clock tsc hpet acpi_pm")
    monkeypatch.setattr(clocksource_audit, "_CLOCK_ROOT", str(tmp_path))
    s = clocksource_audit.status()
    assert s["ok"] is True
    assert s["current"] == "kvm-clock"
    assert s["available"] == ["kvm-clock", "tsc", "hpet", "acpi_pm"]
    assert s["virt"] == "kvm"
    assert s["verdict"]["verdict"] == "optimal"


def test_status_bare_metal_with_tsc(tmp_path, monkeypatch):
    _mk_clocksource(tmp_path, current="tsc",
                    available="tsc hpet acpi_pm")
    monkeypatch.setattr(clocksource_audit, "_CLOCK_ROOT", str(tmp_path))
    s = clocksource_audit.status()
    assert s["verdict"]["verdict"] == "optimal"
    assert s["virt"] is None


def test_status_hpet_active_warns(tmp_path, monkeypatch):
    _mk_clocksource(tmp_path, current="hpet",
                    available="tsc hpet acpi_pm")
    monkeypatch.setattr(clocksource_audit, "_CLOCK_ROOT", str(tmp_path))
    s = clocksource_audit.status()
    assert s["verdict"]["verdict"] == "hpet_active"


def test_status_missing_root_returns_error(tmp_path, monkeypatch):
    monkeypatch.setattr(clocksource_audit, "_CLOCK_ROOT",
                          str(tmp_path / "absent"))
    s = clocksource_audit.status()
    assert s["ok"] is False
    assert s["error"] == "clocksource_unavailable"


def test_status_recipe_contains_grub_cmdline(tmp_path, monkeypatch):
    _mk_clocksource(tmp_path, current="hpet",
                    available="tsc hpet acpi_pm")
    monkeypatch.setattr(clocksource_audit, "_CLOCK_ROOT", str(tmp_path))
    s = clocksource_audit.status()
    rec = s["verdict"]["recommendation"]
    assert "clocksource=tsc" in rec
    assert "GRUB" in rec or "/etc/default/grub" in rec or "update-grub" in rec
