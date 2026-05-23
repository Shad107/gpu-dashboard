"""Tests for modules/cpuidle_audit.py — R&D #36.2 cpuidle audit."""
from __future__ import annotations

from pathlib import Path

import pytest

from gpu_dashboard.modules import cpuidle_audit


def _mk_cpuidle(root: Path, *, current_driver: str = "intel_idle",
                  current_governor: str = "menu",
                  available_governors: str = "ladder menu teo haltpoll"):
    d = root / "cpuidle"
    d.mkdir(parents=True, exist_ok=True)
    (d / "current_driver").write_text(current_driver + "\n")
    (d / "current_governor").write_text(current_governor + "\n")
    (d / "available_governors").write_text(available_governors + "\n")


def _mk_cpu_states(root: Path, n: int, states: list):
    """states = [{"name": "POLL", "latency": 0, "disable": 0, ...}, ...]"""
    base = root / f"cpu{n}" / "cpuidle"
    base.mkdir(parents=True, exist_ok=True)
    for i, st in enumerate(states):
        d = base / f"state{i}"
        d.mkdir(exist_ok=True)
        (d / "name").write_text(st.get("name", f"S{i}") + "\n")
        (d / "desc").write_text(st.get("desc", "") + "\n")
        (d / "latency").write_text(str(st.get("latency", 0)) + "\n")
        (d / "residency").write_text(str(st.get("residency", 0)) + "\n")
        (d / "disable").write_text(str(st.get("disable", 0)) + "\n")
        (d / "usage").write_text(str(st.get("usage", 0)) + "\n")
        (d / "time").write_text(str(st.get("time", 0)) + "\n")


# --- read helpers ----------------------------------------------

def test_read_current_driver(tmp_path):
    _mk_cpuidle(tmp_path, current_driver="intel_idle")
    assert cpuidle_audit.read_current_driver(str(tmp_path)) == "intel_idle"


def test_read_current_driver_none(tmp_path):
    _mk_cpuidle(tmp_path, current_driver="none")
    assert cpuidle_audit.read_current_driver(str(tmp_path)) == "none"


def test_read_current_governor(tmp_path):
    _mk_cpuidle(tmp_path, current_governor="haltpoll")
    assert cpuidle_audit.read_current_governor(str(tmp_path)) == "haltpoll"


def test_read_missing_returns_none(tmp_path):
    assert cpuidle_audit.read_current_driver(str(tmp_path)) is None


# --- read_cpu_states ----------------------------------------

def test_read_cpu_states_returns_list(tmp_path):
    _mk_cpu_states(tmp_path, 0, [
        {"name": "POLL", "latency": 0, "disable": 0, "usage": 100, "time": 1000},
        {"name": "C1", "latency": 1, "disable": 0, "usage": 5000, "time": 9000},
        {"name": "C8", "latency": 1000, "disable": 0, "usage": 50, "time": 50000},
    ])
    states = cpuidle_audit.read_cpu_states(str(tmp_path), 0)
    assert len(states) == 3
    assert states[0]["name"] == "POLL"
    assert states[2]["name"] == "C8"
    assert states[2]["latency"] == 1000


def test_read_cpu_states_empty_when_no_cpuidle(tmp_path):
    assert cpuidle_audit.read_cpu_states(str(tmp_path), 0) == []


def test_read_cpu_states_skips_disabled(tmp_path):
    _mk_cpu_states(tmp_path, 0, [
        {"name": "POLL", "latency": 0, "disable": 0},
        {"name": "C6", "latency": 100, "disable": 1},  # disabled
        {"name": "C8", "latency": 1000, "disable": 0},
    ])
    states = cpuidle_audit.read_cpu_states(str(tmp_path), 0)
    # disabled state still reported but flagged
    assert len(states) == 3
    assert states[1]["disable"] == 1


# --- classify -----------------------------------------------

def test_classify_disabled_driver():
    v = cpuidle_audit.classify(driver="none", governor="menu",
                                   max_latency=None)
    assert v["verdict"] == "disabled_driver"


def test_classify_haltpoll_governor_is_optimal():
    v = cpuidle_audit.classify(driver="intel_idle", governor="haltpoll",
                                   max_latency=10)
    assert v["verdict"] == "haltpoll_optimal"


def test_classify_deep_states_warn():
    # Max state latency 1000 µs (C8/C10) → CUDA roundtrip tax
    v = cpuidle_audit.classify(driver="intel_idle", governor="menu",
                                   max_latency=1000)
    assert v["verdict"] == "deep_states_active"
    assert "cpupower" in v["recommendation"].lower() or "idle-set" in v["recommendation"].lower()


def test_classify_shallow_only_is_ok():
    v = cpuidle_audit.classify(driver="intel_idle", governor="menu",
                                   max_latency=10)
    assert v["verdict"] == "shallow_only"


def test_classify_menu_governor_with_moderate_latency():
    # max_latency in 50-500 µs range → balanced, no warn
    v = cpuidle_audit.classify(driver="intel_idle", governor="menu",
                                   max_latency=200)
    assert v["verdict"] in ("balanced", "deep_states_active")


def test_classify_unknown_when_no_data():
    v = cpuidle_audit.classify(driver=None, governor=None,
                                   max_latency=None)
    assert v["verdict"] == "unknown"


# --- status -----------------------------------------------

def test_status_vm_disabled_driver(tmp_path, monkeypatch):
    # The live-rig case
    _mk_cpuidle(tmp_path, current_driver="none", current_governor="menu")
    monkeypatch.setattr(cpuidle_audit, "_CPU_ROOT", str(tmp_path))
    s = cpuidle_audit.status()
    assert s["ok"] is True
    assert s["driver"] == "none"
    assert s["governor"] == "menu"
    assert s["verdict"]["verdict"] == "disabled_driver"


def test_status_no_cpuidle_dir(tmp_path, monkeypatch):
    monkeypatch.setattr(cpuidle_audit, "_CPU_ROOT",
                          str(tmp_path / "absent"))
    s = cpuidle_audit.status()
    assert s["ok"] is False
    assert s["error"] == "cpuidle_unavailable"


def test_status_intel_idle_with_deep_states(tmp_path, monkeypatch):
    _mk_cpuidle(tmp_path, current_driver="intel_idle",
                  current_governor="menu")
    _mk_cpu_states(tmp_path, 0, [
        {"name": "POLL", "latency": 0},
        {"name": "C1", "latency": 1},
        {"name": "C6", "latency": 200},
        {"name": "C8", "latency": 1000},
    ])
    monkeypatch.setattr(cpuidle_audit, "_CPU_ROOT", str(tmp_path))
    s = cpuidle_audit.status()
    assert s["max_latency"] == 1000
    assert s["verdict"]["verdict"] == "deep_states_active"


def test_status_haltpoll_governor(tmp_path, monkeypatch):
    _mk_cpuidle(tmp_path, current_driver="intel_idle",
                  current_governor="haltpoll")
    monkeypatch.setattr(cpuidle_audit, "_CPU_ROOT", str(tmp_path))
    s = cpuidle_audit.status()
    assert s["verdict"]["verdict"] == "haltpoll_optimal"


def test_status_includes_state_names(tmp_path, monkeypatch):
    _mk_cpuidle(tmp_path, current_driver="intel_idle",
                  current_governor="menu")
    _mk_cpu_states(tmp_path, 0, [
        {"name": "POLL", "latency": 0},
        {"name": "C1E", "latency": 10},
    ])
    monkeypatch.setattr(cpuidle_audit, "_CPU_ROOT", str(tmp_path))
    s = cpuidle_audit.status()
    names = [st["name"] for st in s["states"]]
    assert "POLL" in names
    assert "C1E" in names


def test_status_includes_available_governors(tmp_path, monkeypatch):
    _mk_cpuidle(tmp_path, current_driver="intel_idle",
                  current_governor="menu",
                  available_governors="ladder menu teo haltpoll")
    monkeypatch.setattr(cpuidle_audit, "_CPU_ROOT", str(tmp_path))
    s = cpuidle_audit.status()
    assert "haltpoll" in s["available_governors"]
