"""Tests for modules/thermal_trip_drift_audit.py — R&D #81.4."""
from __future__ import annotations

import pytest

from gpu_dashboard.modules import thermal_trip_drift_audit as mod


def _mk_zone(root, idx, *, type_="acpitz", temp_milli=40000,
              policy="step_wise",
              available_policies="step_wise user_space",
              trips=None):
    """trips: list of (type, temp_milli, hyst_milli)"""
    d = root / f"thermal_zone{idx}"
    d.mkdir(parents=True, exist_ok=True)
    (d / "type").write_text(type_ + "\n")
    (d / "temp").write_text(f"{temp_milli}\n")
    (d / "policy").write_text(policy + "\n")
    (d / "available_policies").write_text(
        available_policies + "\n")
    for i, (t_type, t_temp, t_hyst) in enumerate(trips or []):
        (d / f"trip_point_{i}_type").write_text(t_type + "\n")
        (d / f"trip_point_{i}_temp").write_text(f"{t_temp}\n")
        (d / f"trip_point_{i}_hyst").write_text(f"{t_hyst}\n")


# --- list_zones ------------------------------------------------

def test_list_missing(tmp_path):
    assert mod.list_zones(str(tmp_path / "nope")) == []


def test_list_zones(tmp_path):
    _mk_zone(tmp_path, 0)
    _mk_zone(tmp_path, 1)
    (tmp_path / "cooling_device0").mkdir()  # ignore non-zone
    assert mod.list_zones(str(tmp_path)) == [
        "thermal_zone0", "thermal_zone1"]


# --- read_zone -------------------------------------------------

def test_read_zone_basic(tmp_path):
    _mk_zone(tmp_path, 0, type_="x86_pkg_temp",
              temp_milli=55000,
              trips=[("passive", 80000, 2000),
                       ("critical", 100000, 2000)])
    out = mod.read_zone(str(tmp_path), "thermal_zone0")
    assert out["type"] == "x86_pkg_temp"
    assert out["temp"] == 55000
    assert len(out["trips"]) == 2
    by_type = {t["type"]: t for t in out["trips"]}
    assert by_type["passive"]["temp"] == 80000
    assert by_type["passive"]["hyst"] == 2000


# --- _is_cpu_zone ----------------------------------------------

def test_is_cpu_zone_x86():
    assert mod._is_cpu_zone("x86_pkg_temp") is True


def test_is_cpu_zone_coretemp():
    assert mod._is_cpu_zone("coretemp") is True


def test_is_cpu_zone_arm():
    assert mod._is_cpu_zone("cpu_thermal") is True


def test_is_cpu_zone_battery():
    assert mod._is_cpu_zone("BAT0_thermal") is True


def test_is_cpu_zone_acpitz():
    # generic ACPI thermal zone — NOT a CPU zone
    assert mod._is_cpu_zone("acpitz") is False


def test_is_cpu_zone_none():
    assert mod._is_cpu_zone(None) is False


# --- classify --------------------------------------------------

def test_classify_unknown():
    v = mod.classify([])
    assert v["verdict"] == "unknown"


def _zone(zone="thermal_zone0", type_="x86_pkg_temp",
            temp=55000, policy="step_wise",
            available=None, trips=None):
    return {
        "zone": zone, "type": type_, "temp": temp,
        "policy": policy,
        "available_policies": (
            available if available is not None
            else ["step_wise", "user_space"]),
        "trips": (trips or [
            {"index": 0, "type": "passive",
                "temp": 80000, "hyst": 2000},
            {"index": 1, "type": "critical",
                "temp": 100000, "hyst": 2000}]),
    }


def test_classify_ok():
    v = mod.classify([_zone()])
    assert v["verdict"] == "ok"


def test_classify_trip_below_current():
    z = _zone(temp=85000)  # 85°C, above passive 80°C
    v = mod.classify([z])
    assert v["verdict"] == "trip_below_current_temp"


def test_classify_critical_past_not_flagged():
    # current 85°C past passive (80°C) → still err on passive
    # but critical past would be a different alert ; we only
    # flag non-critical trips.
    z = _zone(temp=105000, trips=[
        {"index": 0, "type": "critical",
            "temp": 100000, "hyst": 2000}])
    # No non-critical trips past current → falls through.
    v = mod.classify([z])
    # x86_pkg_temp without passive trip → cpu zone accent
    assert v["verdict"] == "passive_disabled_on_cpu_zone"


def test_classify_hyst_zero():
    z = _zone(trips=[
        {"index": 0, "type": "passive",
            "temp": 80000, "hyst": 0},
        {"index": 1, "type": "critical",
            "temp": 100000, "hyst": 2000}])
    v = mod.classify([z])
    assert v["verdict"] == "hyst_zero_oscillation_risk"


def test_classify_hyst_zero_critical_ok():
    # critical trip with hyst=0 is normal (no oscillation
    # because we shutdown)
    z = _zone(trips=[
        {"index": 0, "type": "passive",
            "temp": 80000, "hyst": 2000},
        {"index": 1, "type": "critical",
            "temp": 100000, "hyst": 0}])
    v = mod.classify([z])
    assert v["verdict"] == "ok"


def test_classify_passive_disabled_on_cpu_zone():
    z = _zone(trips=[
        {"index": 0, "type": "critical",
            "temp": 100000, "hyst": 2000}])
    v = mod.classify([z])
    assert v["verdict"] == "passive_disabled_on_cpu_zone"


def test_classify_passive_disabled_only_on_cpu_zone():
    # non-CPU zone with no passive trip is fine
    z = _zone(type_="acpitz", trips=[
        {"index": 0, "type": "critical",
            "temp": 100000, "hyst": 2000}])
    v = mod.classify([z])
    assert v["verdict"] == "ok"


def test_classify_policy_user_space():
    z = _zone(policy="user_space")
    v = mod.classify([z])
    assert v["verdict"] == "policy_user_space_idle"


# Priority : trip_below > hyst_zero > passive_disabled > policy
def test_priority_trip_below_over_hyst_zero():
    z = _zone(temp=85000, trips=[
        {"index": 0, "type": "passive",
            "temp": 80000, "hyst": 0},
        {"index": 1, "type": "critical",
            "temp": 100000, "hyst": 2000}])
    v = mod.classify([z])
    assert v["verdict"] == "trip_below_current_temp"


def test_priority_hyst_zero_over_passive_disabled():
    z1 = _zone(zone="thermal_zone0", trips=[
        {"index": 0, "type": "passive",
            "temp": 80000, "hyst": 0},
        {"index": 1, "type": "critical",
            "temp": 100000, "hyst": 2000}])
    z2 = _zone(zone="thermal_zone1", trips=[
        {"index": 0, "type": "critical",
            "temp": 100000, "hyst": 2000}])
    v = mod.classify([z1, z2])
    assert v["verdict"] == "hyst_zero_oscillation_risk"


# --- status integration ----------------------------------------

def test_status_unknown(tmp_path):
    out = mod.status(None, str(tmp_path / "nope"))
    assert out["ok"] is False
    assert out["verdict"]["verdict"] == "unknown"


def test_status_ok_synthetic(tmp_path):
    _mk_zone(tmp_path, 0, type_="x86_pkg_temp",
              temp_milli=55000,
              trips=[("passive", 80000, 2000),
                       ("critical", 100000, 2000)])
    out = mod.status(None, str(tmp_path))
    assert out["ok"] is True
    assert out["zone_count"] == 1
    assert out["verdict"]["verdict"] == "ok"


def test_status_trip_below_synthetic(tmp_path):
    _mk_zone(tmp_path, 0, type_="x86_pkg_temp",
              temp_milli=85000,
              trips=[("passive", 80000, 2000),
                       ("critical", 100000, 2000)])
    out = mod.status(None, str(tmp_path))
    assert out["ok"] is False
    assert out["verdict"]["verdict"] == "trip_below_current_temp"
