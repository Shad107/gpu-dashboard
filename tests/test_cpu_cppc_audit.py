"""Tests for modules/cpu_cppc_audit.py — R&D #77.1."""
from __future__ import annotations

import pytest

from gpu_dashboard.modules import cpu_cppc_audit as mod


def _mk_cpu_cppc(root, cpu, **knobs):
    d = root / f"cpu{cpu}" / "acpi_cppc"
    d.mkdir(parents=True, exist_ok=True)
    for k, v in knobs.items():
        (d / k).write_text(f"{v}\n")


def _mk_cpu_scaling_driver(root, cpu, driver):
    d = root / f"cpu{cpu}" / "cpufreq"
    d.mkdir(parents=True, exist_ok=True)
    (d / "scaling_driver").write_text(driver + "\n")


# --- list_cpus -------------------------------------------------

def test_list_cpus_missing(tmp_path):
    assert mod.list_cpus(str(tmp_path / "nope")) == []


def test_list_cpus(tmp_path):
    for c in (0, 1, 2):
        (tmp_path / f"cpu{c}").mkdir()
    assert mod.list_cpus(str(tmp_path)) == [0, 1, 2]


# --- read_cppc --------------------------------------------------

def test_read_cppc_missing(tmp_path):
    out = mod.read_cppc(str(tmp_path), 0)
    assert all(v is None for v in out.values())


def test_read_cppc_populated(tmp_path):
    _mk_cpu_cppc(tmp_path, 0,
                       highest_perf=255, nominal_perf=180,
                       lowest_perf=20, nominal_freq=3500,
                       lowest_freq=400, reference_perf=100,
                       wraparound_time=315360000)
    out = mod.read_cppc(str(tmp_path), 0)
    assert out["highest_perf"] == 255
    assert out["nominal_freq"] == 3500


# --- classify ---------------------------------------------------

def _good_cppc():
    return {"highest_perf": 255, "nominal_perf": 180,
              "lowest_nonlinear_perf": 50, "lowest_perf": 20,
              "nominal_freq": 3500, "lowest_freq": 400,
              "reference_perf": 100,
              "wraparound_time": 315360000}


def _all_none():
    return {k: None for k in mod._KNOBS}


def test_classify_unknown():
    v = mod.classify(False, {}, None)
    assert v["verdict"] == "unknown"


def test_classify_cppc_absent():
    v = mod.classify(True,
                          {0: _all_none(), 1: _all_none()},
                          "intel_pstate")
    assert v["verdict"] == "cppc_absent"


def test_classify_ok():
    v = mod.classify(True, {0: _good_cppc()}, "intel_pstate")
    assert v["verdict"] == "ok"


def test_classify_clamped():
    c = _good_cppc()
    c["nominal_perf"] = c["highest_perf"]  # clamped
    v = mod.classify(True, {0: c}, "intel_pstate")
    assert v["verdict"] == "cppc_clamped"


def test_classify_frequency_inversion():
    c = _good_cppc()
    c["nominal_freq"] = 200
    c["lowest_freq"] = 400
    v = mod.classify(True, {0: c}, "intel_pstate")
    assert v["verdict"] == "frequency_inversion"


def test_classify_driver_ignoring_cppc():
    v = mod.classify(True, {0: _good_cppc()},
                          "acpi-cpufreq")
    assert v["verdict"] == "driver_ignoring_cppc"


def test_classify_cppc_driver_ok():
    v = mod.classify(True, {0: _good_cppc()}, "cppc_cpufreq")
    assert v["verdict"] == "ok"


def test_classify_amd_pstate_ok():
    v = mod.classify(True, {0: _good_cppc()},
                          "amd-pstate-epp")
    assert v["verdict"] == "ok"


# Priority : clamped > inversion > driver_ignoring
def test_priority_clamped_over_inversion():
    c = _good_cppc()
    c["nominal_perf"] = c["highest_perf"]
    c["nominal_freq"] = 200
    c["lowest_freq"] = 400
    v = mod.classify(True, {0: c}, "intel_pstate")
    assert v["verdict"] == "cppc_clamped"


def test_priority_inversion_over_driver():
    c = _good_cppc()
    c["nominal_freq"] = 200
    c["lowest_freq"] = 400
    v = mod.classify(True, {0: c}, "acpi-cpufreq")
    assert v["verdict"] == "frequency_inversion"


# --- status integration ----------------------------------------

def test_status_unknown(tmp_path):
    out = mod.status(None, str(tmp_path / "nope"))
    assert out["ok"] is False
    assert out["verdict"]["verdict"] == "unknown"


def test_status_absent(tmp_path):
    (tmp_path / "cpu0").mkdir()
    (tmp_path / "cpu1").mkdir()
    out = mod.status(None, str(tmp_path))
    assert out["verdict"]["verdict"] == "cppc_absent"


def test_status_ok_synthetic(tmp_path):
    _mk_cpu_cppc(tmp_path, 0, **_good_cppc())
    _mk_cpu_scaling_driver(tmp_path, 0, "intel_pstate")
    out = mod.status(None, str(tmp_path))
    assert out["cpu_count"] == 1
    assert out["scaling_driver"] == "intel_pstate"
    assert out["verdict"]["verdict"] == "ok"


def test_status_clamped_synthetic(tmp_path):
    c = _good_cppc()
    c["nominal_perf"] = c["highest_perf"]
    _mk_cpu_cppc(tmp_path, 0, **c)
    _mk_cpu_scaling_driver(tmp_path, 0, "intel_pstate")
    out = mod.status(None, str(tmp_path))
    assert out["verdict"]["verdict"] == "cppc_clamped"
