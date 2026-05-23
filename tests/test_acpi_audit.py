"""Tests for modules/acpi_audit.py — R&D #47.2."""
from __future__ import annotations

from pathlib import Path

import pytest

from gpu_dashboard.modules import acpi_audit as mod


WAKEUP_SAMPLE = """\
Device	S-state	  Status   Sysfs node
XHC	S3	*enabled	pci:0000:00:14.0
RP05	S3	*enabled	pci:0000:00:1c.0
PEG0	S3	*disabled	pci:0000:00:01.0
"""


# --- read_platform_profile ----------------------------------------

def test_read_platform_profile_present(tmp_path):
    (tmp_path / "platform_profile").write_text("performance\n")
    (tmp_path / "platform_profile_choices").write_text(
        "low-power balanced balanced-performance performance\n")
    (tmp_path / "pm_profile").write_text("7\n")
    out = mod.read_platform_profile(str(tmp_path))
    assert out["current"] == "performance"
    assert "balanced-performance" in out["choices"]
    assert out["pm_profile"] == 7


def test_read_platform_profile_missing(tmp_path):
    out = mod.read_platform_profile(str(tmp_path / "nope"))
    assert out == {}


# --- parse_gpe ----------------------------------------------------

def test_parse_gpe_basic():
    g = mod.parse_gpe("       0  EN     enabled      unmasked\n")
    assert g == {"count": 0, "flag": "EN"}


def test_parse_gpe_count_present():
    g = mod.parse_gpe("    12345  EN  enabled\n")
    assert g["count"] == 12345


def test_parse_gpe_empty():
    assert mod.parse_gpe("") == {}
    assert mod.parse_gpe(None) == {}


# --- walk_interrupts ----------------------------------------------

def test_walk_interrupts(tmp_path):
    irq = tmp_path / "interrupts"
    irq.mkdir()
    (irq / "gpe00").write_text("       0   EN  enabled\n")
    (irq / "gpe17").write_text("   50000   EN  enabled\n")
    (irq / "ff_pwr_btn").write_text("       0  invalid  unmasked\n")
    out = mod.walk_interrupts(str(tmp_path))
    names = {g["name"] for g in out}
    assert "gpe00" in names and "gpe17" in names
    gpe17 = next(g for g in out if g["name"] == "gpe17")
    assert gpe17["count"] == 50000


def test_walk_interrupts_missing(tmp_path):
    assert mod.walk_interrupts(str(tmp_path / "nope")) == []


# --- parse_wakeup -------------------------------------------------

def test_parse_wakeup_basic():
    out = mod.parse_wakeup(WAKEUP_SAMPLE)
    assert len(out) == 3
    xhc = next(w for w in out if w["device"] == "XHC")
    assert xhc["enabled"] is True
    peg = next(w for w in out if w["device"] == "PEG0")
    assert peg["enabled"] is False


def test_parse_wakeup_empty():
    assert mod.parse_wakeup("") == []
    assert mod.parse_wakeup(None) == []


# --- classify -----------------------------------------------------

def test_classify_unknown():
    v = mod.classify({}, [], [])
    assert v["verdict"] == "unknown"


def test_classify_no_platform_profile():
    v = mod.classify({"pm_profile": 0},
                       [{"device": "XHC", "enabled": True,
                          "status": "*enabled", "s_state": "S3"}],
                       [{"name": "gpe00", "count": 0, "flag": "EN"}])
    assert v["verdict"] == "no_platform_profile"


def test_classify_ok():
    v = mod.classify({"current": "balanced-performance",
                       "pm_profile": 1,
                       "choices": ["balanced", "performance"]},
                       [{"device": "XHC", "enabled": False,
                          "status": "*disabled", "s_state": "S3"}],
                       [{"name": "gpe00", "count": 0, "flag": "EN"}])
    assert v["verdict"] == "ok"


def test_classify_gpe_storm():
    v = mod.classify({"current": "performance"}, [],
                       [{"name": "gpe17", "count": 50000, "flag": "EN"}])
    assert v["verdict"] == "gpe_storm"
    assert "gpe17" in v["reason"]


def test_classify_pcie_root_wakeup():
    v = mod.classify({"current": "performance"},
                       [{"device": "RP05", "enabled": True,
                          "status": "*enabled", "s_state": "S3"}],
                       [])
    assert v["verdict"] == "pcie_root_wakeup"
    assert "RP05" in v["reason"]


def test_classify_quiet_profile_workstation():
    v = mod.classify({"current": "quiet", "pm_profile": 7,
                       "choices": ["quiet", "balanced-performance"]},
                       [], [])
    assert v["verdict"] == "quiet_profile_on_workstation"


def test_classify_quiet_on_desktop_not_flagged():
    # pm_profile=1 (desktop) should NOT trigger ; quiet may be OK.
    v = mod.classify({"current": "quiet", "pm_profile": 1,
                       "choices": ["quiet", "balanced"]},
                       [], [])
    assert v["verdict"] == "ok"


def test_classify_storm_wins_over_root_wakeup():
    v = mod.classify({"current": "performance"},
                       [{"device": "RP05", "enabled": True,
                          "status": "*enabled", "s_state": "S3"}],
                       [{"name": "gpe17", "count": 50000, "flag": "EN"}])
    assert v["verdict"] == "gpe_storm"


# --- status integration -------------------------------------------

def test_status_with_isolated(monkeypatch, tmp_path):
    acpi = tmp_path / "acpi"
    acpi.mkdir()
    (acpi / "pm_profile").write_text("0\n")
    irq = acpi / "interrupts"
    irq.mkdir()
    (irq / "gpe00").write_text("       0  EN  enabled\n")
    wakeup = tmp_path / "wakeup"
    wakeup.write_text(WAKEUP_SAMPLE)
    monkeypatch.setattr(mod, "_SYS_ACPI", str(acpi))
    monkeypatch.setattr(mod, "_PROC_ACPI_WAKEUP", str(wakeup))
    out = mod.status()
    assert out["ok"] is True
    # platform_profile absent + RP05 wakeup enabled → pcie_root_wakeup wins
    assert out["verdict"]["verdict"] == "pcie_root_wakeup"


def test_status_unknown(monkeypatch, tmp_path):
    monkeypatch.setattr(mod, "_SYS_ACPI", str(tmp_path / "nope"))
    monkeypatch.setattr(mod, "_PROC_ACPI_WAKEUP",
                        str(tmp_path / "nope2"))
    out = mod.status()
    assert out["ok"] is False
    assert out["verdict"]["verdict"] == "unknown"
