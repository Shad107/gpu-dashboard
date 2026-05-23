"""Tests for modules/rapl_power_cap_audit.py — R&D #53.4."""
from __future__ import annotations

import pytest

from gpu_dashboard.modules import rapl_power_cap_audit as mod


def _mk_zone(root, id_, *, name="package-0", enabled=1,
              pl1_uw=125_000_000, pl1_window_us=1_000_000,
              pl2_uw=200_000_000, max_uw=125_000_000):
    d = root / id_
    d.mkdir(parents=True, exist_ok=True)
    (d / "name").write_text(name + "\n")
    (d / "enabled").write_text(f"{enabled}\n")
    (d / "constraint_0_power_limit_uw").write_text(f"{pl1_uw}\n")
    (d / "constraint_0_time_window_us").write_text(
        f"{pl1_window_us}\n")
    (d / "constraint_1_power_limit_uw").write_text(f"{pl2_uw}\n")
    (d / "max_power_range_uw").write_text(f"{max_uw}\n")


def _mk_cpu(root, idx, *, governor="performance"):
    cf = root / f"cpu{idx}" / "cpufreq"
    cf.mkdir(parents=True, exist_ok=True)
    (cf / "scaling_governor").write_text(governor + "\n")


# --- list_rapl_zones --------------------------------------------

def test_list_rapl_zones_missing(tmp_path):
    assert mod.list_rapl_zones(str(tmp_path / "nope")) == []


def test_list_rapl_zones(tmp_path):
    _mk_zone(tmp_path, "intel-rapl:0")
    _mk_zone(tmp_path, "intel-rapl:0:0", name="core")
    out = mod.list_rapl_zones(str(tmp_path))
    ids = sorted(z["id"] for z in out)
    assert ids == ["intel-rapl:0", "intel-rapl:0:0"]


def test_list_rapl_zones_ignores_other(tmp_path):
    _mk_zone(tmp_path, "intel-rapl:0")
    (tmp_path / "dtpm").mkdir()
    out = mod.list_rapl_zones(str(tmp_path))
    assert len(out) == 1


# --- list_governors ---------------------------------------------

def test_list_governors(tmp_path):
    _mk_cpu(tmp_path, 0)
    _mk_cpu(tmp_path, 1, governor="powersave")
    out = mod.list_governors(str(tmp_path))
    assert out == {0: "performance", 1: "powersave"}


def test_list_governors_missing(tmp_path):
    assert mod.list_governors(str(tmp_path / "nope")) == {}


# --- read_turbo_state -------------------------------------------

def test_read_turbo_state(tmp_path):
    boost = tmp_path / "boost"
    boost.write_text("1\n")
    ip = tmp_path / "intel_pstate"
    ip.mkdir()
    (ip / "no_turbo").write_text("0\n")
    out = mod.read_turbo_state(str(boost), str(ip))
    assert out["cpufreq_boost"] == 1
    assert out["intel_pstate_no_turbo"] == 0


# --- classify ---------------------------------------------------

def _zone_package(pl1_uw=125_000_000, max_uw=125_000_000):
    return {"id": "intel-rapl:0", "name": "package-0", "enabled": 1,
              "constraint_0_power_limit_uw": pl1_uw,
              "constraint_0_time_window_us": 1_000_000,
              "constraint_1_power_limit_uw": 200_000_000,
              "max_power_range_uw": max_uw}


def _zone_psys(pl1_uw):
    return {"id": "intel-rapl:1", "name": "psys", "enabled": 1,
              "constraint_0_power_limit_uw": pl1_uw,
              "constraint_0_time_window_us": 1_000_000,
              "constraint_1_power_limit_uw": None,
              "max_power_range_uw": None}


def test_classify_unknown():
    v = mod.classify([], {}, {})
    assert v["verdict"] == "unknown"


def test_classify_ok():
    v = mod.classify([_zone_package()],
                       {0: "performance", 1: "performance"},
                       {"cpufreq_boost": 1})
    assert v["verdict"] == "ok"


def test_classify_pl1_below_tdp():
    # PL1 = 45 W vs max_power_range = 125 W → < 80 %
    v = mod.classify([_zone_package(pl1_uw=45_000_000)],
                       {0: "performance"},
                       {"cpufreq_boost": 1})
    assert v["verdict"] == "pl1_below_tdp_throttling"


def test_classify_governor_mixed():
    v = mod.classify([_zone_package()],
                       {0: "performance", 1: "powersave"},
                       {"cpufreq_boost": 1})
    assert v["verdict"] == "governor_powersave_mixed"


def test_classify_turbo_disabled_no_turbo():
    v = mod.classify([_zone_package()],
                       {0: "performance"},
                       {"intel_pstate_no_turbo": 1,
                          "cpufreq_boost": 1})
    assert v["verdict"] == "turbo_disabled_silently"


def test_classify_turbo_disabled_boost():
    v = mod.classify([_zone_package()],
                       {0: "performance"},
                       {"cpufreq_boost": 0})
    assert v["verdict"] == "turbo_disabled_silently"


def test_classify_psys_cap():
    v = mod.classify(
        [_zone_package(pl1_uw=125_000_000),
           _zone_psys(pl1_uw=80_000_000)],
        {0: "performance"},
        {"cpufreq_boost": 1})
    assert v["verdict"] == "psys_cap_active"


def test_classify_priority_pl1_wins():
    v = mod.classify(
        [_zone_package(pl1_uw=45_000_000),
           _zone_psys(pl1_uw=30_000_000)],
        {0: "powersave", 1: "performance"},
        {"cpufreq_boost": 0})
    assert v["verdict"] == "pl1_below_tdp_throttling"


# --- status integration -----------------------------------------

def test_status_unknown_vm(tmp_path):
    out = mod.status(None, str(tmp_path / "nopowercap"),
                       str(tmp_path / "nocpu"),
                       str(tmp_path / "noboost"),
                       str(tmp_path / "nopstate"))
    assert out["ok"] is False
    assert out["verdict"]["verdict"] == "unknown"


def test_status_live_like(tmp_path):
    pc = tmp_path / "powercap"
    cpu = tmp_path / "cpu"
    _mk_zone(pc, "intel-rapl:0", pl1_uw=45_000_000,
              max_uw=125_000_000)
    _mk_cpu(cpu, 0)
    _mk_cpu(cpu, 1)
    boost = tmp_path / "boost"
    boost.write_text("1\n")
    out = mod.status(None, str(pc), str(cpu), str(boost),
                       str(tmp_path / "noip"))
    assert out["ok"] is True
    assert out["zone_count"] == 1
    assert out["cpu_count"] == 2
    assert out["verdict"]["verdict"] == "pl1_below_tdp_throttling"
    # governor_histogram has been correctly populated
    assert out["governor_histogram"] == {"performance": 2}
