"""Tests for modules/cpu_boost.py — R&D #35.1 CPU turbo/boost auditor."""
from __future__ import annotations

from pathlib import Path

import pytest

from gpu_dashboard.modules import cpu_boost


def _mk_cpu_dirs(root: Path):
    root.mkdir(parents=True, exist_ok=True)
    return root


def _mk_cpufreq_boost(root: Path, value: str):
    cf = root / "cpufreq"
    cf.mkdir(parents=True, exist_ok=True)
    (cf / "boost").write_text(value + "\n")


def _mk_intel_pstate(root: Path, *, no_turbo: str | None = None,
                       status: str | None = None):
    d = root / "intel_pstate"
    d.mkdir(parents=True, exist_ok=True)
    if no_turbo is not None:
        (d / "no_turbo").write_text(no_turbo + "\n")
    if status is not None:
        (d / "status").write_text(status + "\n")


def _mk_amd_pstate(root: Path, status: str):
    d = root / "amd_pstate"
    d.mkdir(parents=True, exist_ok=True)
    (d / "status").write_text(status + "\n")


# --- read helpers --------------------------------------------------

def test_read_cpufreq_boost_returns_int(tmp_path):
    _mk_cpufreq_boost(tmp_path, "1")
    assert cpu_boost.read_cpufreq_boost(str(tmp_path)) == 1


def test_read_cpufreq_boost_missing_returns_none(tmp_path):
    assert cpu_boost.read_cpufreq_boost(str(tmp_path)) is None


def test_read_intel_no_turbo(tmp_path):
    _mk_intel_pstate(tmp_path, no_turbo="1")
    assert cpu_boost.read_intel_no_turbo(str(tmp_path)) == 1


def test_read_intel_status(tmp_path):
    _mk_intel_pstate(tmp_path, status="active")
    assert cpu_boost.read_intel_status(str(tmp_path)) == "active"


def test_read_amd_status(tmp_path):
    _mk_amd_pstate(tmp_path, "active")
    assert cpu_boost.read_amd_status(str(tmp_path)) == "active"


# --- detect_mode --------------------------------------------------

def test_detect_mode_intel_pstate(tmp_path):
    _mk_intel_pstate(tmp_path, status="active")
    assert cpu_boost.detect_mode(str(tmp_path)) == "intel_pstate"


def test_detect_mode_amd_pstate(tmp_path):
    _mk_amd_pstate(tmp_path, "active")
    assert cpu_boost.detect_mode(str(tmp_path)) == "amd_pstate"


def test_detect_mode_generic_cpufreq(tmp_path):
    _mk_cpufreq_boost(tmp_path, "1")
    assert cpu_boost.detect_mode(str(tmp_path)) == "cpufreq_boost"


def test_detect_mode_missing(tmp_path):
    assert cpu_boost.detect_mode(str(tmp_path)) == "missing"


# --- classify -----------------------------------------------------

def test_classify_enabled_via_cpufreq_boost():
    v = cpu_boost.classify(mode="cpufreq_boost", boost=1, no_turbo=None,
                              intel_status=None, amd_status=None)
    assert v["verdict"] == "boost_enabled"


def test_classify_disabled_via_cpufreq_boost():
    v = cpu_boost.classify(mode="cpufreq_boost", boost=0, no_turbo=None,
                              intel_status=None, amd_status=None)
    assert v["verdict"] == "boost_disabled"
    assert "30" in v["reason"] or "turbo" in v["reason"].lower()


def test_classify_intel_pstate_turbo_enabled():
    v = cpu_boost.classify(mode="intel_pstate", boost=None, no_turbo=0,
                              intel_status="active", amd_status=None)
    assert v["verdict"] == "boost_enabled"


def test_classify_intel_pstate_turbo_disabled():
    # no_turbo=1 means turbo IS disabled
    v = cpu_boost.classify(mode="intel_pstate", boost=None, no_turbo=1,
                              intel_status="active", amd_status=None)
    assert v["verdict"] == "boost_disabled"
    assert "no_turbo" in v["recommendation"]


def test_classify_intel_pstate_passive():
    # status=passive means intel_pstate is not the active governor —
    # may fall back to acpi-cpufreq
    v = cpu_boost.classify(mode="intel_pstate", boost=None, no_turbo=0,
                              intel_status="passive", amd_status=None)
    assert v["verdict"] in ("boost_enabled", "passive")


def test_classify_missing_when_no_subsystem():
    v = cpu_boost.classify(mode="missing", boost=None, no_turbo=None,
                              intel_status=None, amd_status=None)
    assert v["verdict"] == "missing"


def test_classify_unknown_when_inconsistent():
    v = cpu_boost.classify(mode="cpufreq_boost", boost=None,
                              no_turbo=None, intel_status=None,
                              amd_status=None)
    assert v["verdict"] == "unknown"


def test_classify_recipe_includes_echo():
    v = cpu_boost.classify(mode="cpufreq_boost", boost=0, no_turbo=None,
                              intel_status=None, amd_status=None)
    assert "echo" in v["recommendation"]
    assert "/sys/devices/system/cpu" in v["recommendation"]


# --- status ------------------------------------------------------

def test_status_vm_no_cpufreq(tmp_path, monkeypatch):
    # The live-rig case: Proxmox guest, no CPU boost sysfs
    monkeypatch.setattr(cpu_boost, "_CPU_ROOT", str(tmp_path))
    s = cpu_boost.status()
    assert s["ok"] is True
    assert s["mode"] == "missing"
    assert s["verdict"]["verdict"] == "missing"


def test_status_amd_with_boost(tmp_path, monkeypatch):
    _mk_cpufreq_boost(tmp_path, "1")
    _mk_amd_pstate(tmp_path, "active")
    monkeypatch.setattr(cpu_boost, "_CPU_ROOT", str(tmp_path))
    s = cpu_boost.status()
    assert s["mode"] == "amd_pstate"
    assert s["boost"] == 1


def test_status_intel_with_turbo_disabled(tmp_path, monkeypatch):
    _mk_intel_pstate(tmp_path, no_turbo="1", status="active")
    monkeypatch.setattr(cpu_boost, "_CPU_ROOT", str(tmp_path))
    s = cpu_boost.status()
    assert s["mode"] == "intel_pstate"
    assert s["no_turbo"] == 1
    assert s["verdict"]["verdict"] == "boost_disabled"


def test_status_intel_with_turbo_enabled(tmp_path, monkeypatch):
    _mk_intel_pstate(tmp_path, no_turbo="0", status="active")
    monkeypatch.setattr(cpu_boost, "_CPU_ROOT", str(tmp_path))
    s = cpu_boost.status()
    assert s["verdict"]["verdict"] == "boost_enabled"


def test_status_generic_boost_enabled(tmp_path, monkeypatch):
    _mk_cpufreq_boost(tmp_path, "1")
    monkeypatch.setattr(cpu_boost, "_CPU_ROOT", str(tmp_path))
    s = cpu_boost.status()
    assert s["verdict"]["verdict"] == "boost_enabled"
