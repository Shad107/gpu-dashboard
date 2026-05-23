"""Tests for modules/cgroup_memevents_audit.py — R&D #50.2."""
from __future__ import annotations

import pytest

from gpu_dashboard.modules import cgroup_memevents_audit as mod


def _mk_unit(root, *path, events=None, swap_events=None,
              peak=None):
    d = root.joinpath(*path)
    d.mkdir(parents=True, exist_ok=True)
    ev = "\n".join(f"{k} {v}" for k, v in (events or {}).items()) + "\n"
    (d / "memory.events").write_text(ev)
    if swap_events is not None:
        sev = "\n".join(f"{k} {v}"
                          for k, v in swap_events.items()) + "\n"
        (d / "memory.swap.events").write_text(sev)
    if peak is not None:
        (d / "memory.peak").write_text(str(peak) + "\n")


# --- parse_kv -----------------------------------------------------

def test_parse_kv_basic():
    out = mod.parse_kv("low 0\nhigh 5\noom 0\noom_kill 2\n")
    assert out == {"low": 0, "high": 5, "oom": 0, "oom_kill": 2}


def test_parse_kv_empty():
    assert mod.parse_kv("") == {}
    assert mod.parse_kv(None) == {}


def test_parse_kv_skips_garbage():
    out = mod.parse_kv("ok 1\ngarbage\nfine 2 extra\nfoo bar\n")
    assert out == {"ok": 1}


# --- walk_units ---------------------------------------------------

def test_walk_units_basic(tmp_path):
    # cgroup root
    (tmp_path / "cgroup.controllers").write_text("memory cpu io\n")
    _mk_unit(tmp_path, "system.slice", "foo.service",
              events={"oom_kill": 0, "high": 2},
              peak=1024 * 1024 * 100)
    _mk_unit(tmp_path, "system.slice", "bar.service",
              events={"oom_kill": 3, "high": 0},
              peak=1024 * 1024 * 50)
    out = mod.walk_units(str(tmp_path))
    assert len(out) == 2


def test_walk_units_skips_dirs_without_memory_events(tmp_path):
    (tmp_path / "init.scope").mkdir()
    _mk_unit(tmp_path, "system.slice",
              events={"oom_kill": 0})
    out = mod.walk_units(str(tmp_path))
    assert len(out) == 1


def test_walk_units_missing(tmp_path):
    assert mod.walk_units(str(tmp_path / "nope")) == []


# --- classify -----------------------------------------------------

def _u(path="x.service", events=None, swap_events=None, peak=0):
    return {"path": path, "events": events or {},
              "swap_events": swap_events or {}, "peak_bytes": peak}


def test_classify_no_cgroup_v2():
    v = mod.classify([])
    assert v["verdict"] == "no_cgroup_v2"


def test_classify_ok():
    v = mod.classify([_u(events={"high": 0, "oom_kill": 0})])
    assert v["verdict"] == "ok"


def test_classify_oom_in_unit():
    v = mod.classify([_u(events={"oom_kill": 5})])
    assert v["verdict"] == "oom_in_unit"
    assert "5" in v["reason"]


def test_classify_swap_failures():
    v = mod.classify([_u(swap_events={"fail": 10})])
    assert v["verdict"] == "swap_failures"


def test_classify_high_pressure():
    units = [_u(path=f"u{i}", events={"high": 1}) for i in range(6)]
    v = mod.classify(units)
    assert v["verdict"] == "high_pressure"


def test_classify_pressure_skipped_below_threshold():
    units = [_u(path=f"u{i}", events={"high": 1}) for i in range(3)]
    v = mod.classify(units)
    assert v["verdict"] == "ok"


def test_classify_priority_oom_wins():
    units = [_u(events={"oom_kill": 1}),
              _u(swap_events={"fail": 5}),
              _u(events={"high": 1}), _u(events={"high": 1}),
              _u(events={"high": 1}), _u(events={"high": 1}),
              _u(events={"high": 1})]
    v = mod.classify(units)
    assert v["verdict"] == "oom_in_unit"


# --- status integration ------------------------------------------

def test_status_no_cgroup(monkeypatch, tmp_path):
    monkeypatch.setattr(mod, "_SYS_CGROUP", str(tmp_path / "nope"))
    out = mod.status()
    assert out["ok"] is False
    assert out["verdict"]["verdict"] == "no_cgroup_v2"


def test_status_with_units(monkeypatch, tmp_path):
    cgroup = tmp_path / "cgroup"
    cgroup.mkdir()
    (cgroup / "cgroup.controllers").write_text("memory\n")
    _mk_unit(cgroup, "system.slice", "ollama.service",
              events={"oom_kill": 1, "high": 0}, peak=10000000)
    monkeypatch.setattr(mod, "_SYS_CGROUP", str(cgroup))
    out = mod.status()
    assert out["ok"] is True
    assert out["unit_count"] == 1
    assert out["verdict"]["verdict"] == "oom_in_unit"
