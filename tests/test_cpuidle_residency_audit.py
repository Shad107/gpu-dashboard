"""Tests for modules/cpuidle_residency_audit.py — R&D #65.1."""
from __future__ import annotations

import pytest

from gpu_dashboard.modules import cpuidle_residency_audit as mod


def _mk_state(root, cpu, state_idx, *, name="C1", disable=0,
                time=1000, usage=10, residency=10,
                above=0, below=0):
    d = root / f"cpu{cpu}" / "cpuidle" / f"state{state_idx}"
    d.mkdir(parents=True, exist_ok=True)
    (d / "name").write_text(name + "\n")
    (d / "disable").write_text(f"{disable}\n")
    (d / "time").write_text(f"{time}\n")
    (d / "usage").write_text(f"{usage}\n")
    (d / "residency").write_text(f"{residency}\n")
    (d / "above").write_text(f"{above}\n")
    (d / "below").write_text(f"{below}\n")


# --- list_cpus + read_states_for_cpu ----------------------------

def test_list_cpus_missing(tmp_path):
    assert mod.list_cpus(str(tmp_path / "nope")) == []


def test_list_cpus(tmp_path):
    _mk_state(tmp_path, 0, 0)
    _mk_state(tmp_path, 1, 0)
    out = mod.list_cpus(str(tmp_path))
    assert out == [0, 1]


def test_read_states_for_cpu(tmp_path):
    _mk_state(tmp_path, 0, 0, name="POLL", time=500)
    _mk_state(tmp_path, 0, 1, name="C1", time=200)
    out = mod.read_states_for_cpu(0, str(tmp_path))
    assert len(out) == 2
    assert out[0]["name"] == "POLL"


# --- classify ---------------------------------------------------

def _build_cpu_states(time_by_state, disable_by_state=None,
                        above_by_state=None,
                        below_by_state=None):
    """Helper: build a single-CPU state list."""
    disable_by_state = disable_by_state or {}
    above_by_state = above_by_state or {}
    below_by_state = below_by_state or {}
    states = []
    for i, (name, t) in enumerate(time_by_state):
        states.append({
            "id": f"state{i}", "idx": i, "name": name,
            "disable": disable_by_state.get(name, 0),
            "residency": 10, "time": t, "usage": 1,
            "above": above_by_state.get(name, 0),
            "below": below_by_state.get(name, 0),
        })
    return states


def test_classify_unknown():
    v = mod.classify({})
    assert v["verdict"] == "unknown"


def test_classify_ok_balanced():
    # POLL 5 %, C1 30 %, C6 65 % → ok
    states = _build_cpu_states(
        [("POLL", 50), ("C1", 300), ("C6", 650)])
    v = mod.classify({0: states})
    assert v["verdict"] == "ok"


def test_classify_poll_dominant():
    states = _build_cpu_states(
        [("POLL", 900), ("C1", 50), ("C6", 50)])
    v = mod.classify({0: states})
    assert v["verdict"] == "poll_dominant"


def test_classify_c6_starved():
    # POLL 10 %, C1 87 %, C6 3 % → c6 starved
    states = _build_cpu_states(
        [("POLL", 100), ("C1", 870), ("C6", 30)])
    v = mod.classify({0: states})
    assert v["verdict"] == "c6_starved"


def test_classify_governor_mispredict():
    # No c6_starved trigger but high above/below skew
    states = _build_cpu_states(
        [("POLL", 50), ("C1", 300), ("C6", 650)],
        above_by_state={"C6": 1000},
        below_by_state={"C6": 100})
    v = mod.classify({0: states})
    assert v["verdict"] == "governor_mispredict"


def test_classify_state_disabled_asymmetry():
    cpu0 = _build_cpu_states(
        [("POLL", 50), ("C1", 300), ("C6", 650)],
        disable_by_state={"C6": 0})
    cpu1 = _build_cpu_states(
        [("POLL", 50), ("C1", 300), ("C6", 650)],
        disable_by_state={"C6": 1})
    v = mod.classify({0: cpu0, 1: cpu1})
    assert v["verdict"] == "state_disabled_asymmetry"


# --- status integration -----------------------------------------

def test_status_unknown(tmp_path):
    out = mod.status(None, str(tmp_path / "nope"))
    assert out["ok"] is False
    assert out["verdict"]["verdict"] == "unknown"


def test_status_live_like(tmp_path):
    _mk_state(tmp_path, 0, 0, name="POLL", time=50)
    _mk_state(tmp_path, 0, 1, name="C1", time=300)
    _mk_state(tmp_path, 0, 2, name="C6", time=650)
    out = mod.status(None, str(tmp_path))
    assert out["ok"] is True
    assert out["cpu_count"] == 1
    assert out["state_count_per_cpu"] == 3
    assert out["verdict"]["verdict"] == "ok"
