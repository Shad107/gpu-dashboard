"""Tests for modules/hwp_epp.py — R&D #36.4 HWP EPP audit."""
from __future__ import annotations

from pathlib import Path

import pytest

from gpu_dashboard.modules import hwp_epp


def _mk_cpu_epp(root: Path, n: int, *, pref: str | None = "performance",
                   available: str | None = "default performance balance_performance balance_power power"):
    cf = root / f"cpu{n}" / "cpufreq"
    cf.mkdir(parents=True, exist_ok=True)
    if pref is not None:
        (cf / "energy_performance_preference").write_text(pref + "\n")
    if available is not None:
        (cf / "energy_performance_available_preferences").write_text(available + "\n")


# --- read helpers ----------------------------------------------

def test_read_epp_returns_string(tmp_path):
    _mk_cpu_epp(tmp_path, 0, pref="performance")
    assert hwp_epp.read_epp(str(tmp_path), 0) == "performance"


def test_read_epp_missing_returns_none(tmp_path):
    assert hwp_epp.read_epp(str(tmp_path), 0) is None


def test_read_available_returns_list(tmp_path):
    _mk_cpu_epp(tmp_path, 0, available="performance balance_performance default")
    av = hwp_epp.read_available(str(tmp_path), 0)
    assert av == ["performance", "balance_performance", "default"]


def test_read_available_missing_returns_empty(tmp_path):
    assert hwp_epp.read_available(str(tmp_path), 0) == []


def test_list_cpus_with_epp(tmp_path):
    _mk_cpu_epp(tmp_path, 0)
    _mk_cpu_epp(tmp_path, 1)
    _mk_cpu_epp(tmp_path, 5, pref=None, available=None)
    # cpu5 has cpufreq dir but no EPP — should be skipped
    cpus = hwp_epp.list_cpus_with_epp(str(tmp_path))
    assert cpus == [0, 1]


def test_list_cpus_with_epp_empty(tmp_path):
    assert hwp_epp.list_cpus_with_epp(str(tmp_path)) == []


# --- classify -------------------------------------------------

def test_classify_performance_all_cpus():
    v = hwp_epp.classify(prefs=["performance"] * 12)
    assert v["verdict"] == "performance"


def test_classify_balanced_balance_performance():
    v = hwp_epp.classify(prefs=["balance_performance"] * 12)
    assert v["verdict"] == "balanced"


def test_classify_power_save_when_any_balance_power():
    # Even one CPU on balance_power → warn
    prefs = ["performance"] * 10 + ["balance_power"] * 2
    v = hwp_epp.classify(prefs=prefs)
    assert v["verdict"] == "power_save"


def test_classify_power_save_when_any_power():
    prefs = ["performance"] * 11 + ["power"]
    v = hwp_epp.classify(prefs=prefs)
    assert v["verdict"] == "power_save"


def test_classify_drift_when_mixed_non_power():
    prefs = ["performance"] * 4 + ["balance_performance"] * 8
    v = hwp_epp.classify(prefs=prefs)
    assert v["verdict"] == "drift"


def test_classify_default_is_balanced_synonym():
    # "default" maps to "balance_performance" semantically on most Intel CPUs
    v = hwp_epp.classify(prefs=["default"] * 12)
    assert v["verdict"] in ("balanced", "default_mode")


def test_classify_missing_when_empty():
    v = hwp_epp.classify(prefs=[])
    assert v["verdict"] == "missing"


def test_classify_unknown_for_garbage_values():
    v = hwp_epp.classify(prefs=["weirdo"] * 12)
    assert v["verdict"] in ("unknown", "drift")


def test_classify_recipe_includes_echo_performance():
    prefs = ["balance_power"] * 12
    v = hwp_epp.classify(prefs=prefs)
    assert "echo performance" in v["recommendation"]


# --- status -------------------------------------------------

def test_status_vm_missing(tmp_path, monkeypatch):
    # The live-rig case
    monkeypatch.setattr(hwp_epp, "_CPU_ROOT", str(tmp_path))
    s = hwp_epp.status()
    assert s["ok"] is True
    assert s["cpu_count"] == 0
    assert s["verdict"]["verdict"] == "missing"


def test_status_performance(tmp_path, monkeypatch):
    for i in range(4):
        _mk_cpu_epp(tmp_path, i, pref="performance")
    monkeypatch.setattr(hwp_epp, "_CPU_ROOT", str(tmp_path))
    s = hwp_epp.status()
    assert s["cpu_count"] == 4
    assert s["distinct_prefs"] == ["performance"]
    assert s["verdict"]["verdict"] == "performance"


def test_status_drift(tmp_path, monkeypatch):
    _mk_cpu_epp(tmp_path, 0, pref="performance")
    _mk_cpu_epp(tmp_path, 1, pref="balance_performance")
    monkeypatch.setattr(hwp_epp, "_CPU_ROOT", str(tmp_path))
    s = hwp_epp.status()
    assert sorted(s["distinct_prefs"]) == ["balance_performance", "performance"]
    assert s["verdict"]["verdict"] == "drift"


def test_status_power_save(tmp_path, monkeypatch):
    for i in range(8):
        _mk_cpu_epp(tmp_path, i, pref="performance")
    _mk_cpu_epp(tmp_path, 8, pref="balance_power")
    monkeypatch.setattr(hwp_epp, "_CPU_ROOT", str(tmp_path))
    s = hwp_epp.status()
    assert s["verdict"]["verdict"] == "power_save"


def test_status_includes_available_set(tmp_path, monkeypatch):
    _mk_cpu_epp(tmp_path, 0, pref="performance",
                 available="default performance balance_performance balance_power power")
    monkeypatch.setattr(hwp_epp, "_CPU_ROOT", str(tmp_path))
    s = hwp_epp.status()
    assert "performance" in s["available"]
    assert "power" in s["available"]
