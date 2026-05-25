"""Tests for modules/tracing_instances_audit.py — R&D #96.3."""
from __future__ import annotations

import pytest

from gpu_dashboard.modules import tracing_instances_audit as mod


def _mk_root(tmp_path, instances=None):
    """instances: dict {name: {buffer_size_kb, tracing_on,
    current_tracer}}."""
    d = tmp_path / "tracing"
    d.mkdir(parents=True, exist_ok=True)
    ins = d / "instances"
    ins.mkdir(exist_ok=True)
    if instances:
        for name, attrs in instances.items():
            id_dir = ins / name
            id_dir.mkdir()
            (id_dir / "buffer_size_kb").write_text(
                str(attrs.get("buffer_size_kb", 7000)) + "\n")
            (id_dir / "tracing_on").write_text(
                str(attrs.get("tracing_on", 0)) + "\n")
            (id_dir / "current_tracer").write_text(
                attrs.get("current_tracer", "nop") + "\n")
    return str(d)


# --- read_instance ---------------------------------------------

def test_read_instance(tmp_path):
    _mk_root(tmp_path, {"bpftrace": {
        "buffer_size_kb": 32768, "tracing_on": 1,
        "current_tracer": "function"}})
    out = mod.read_instance(
        str(tmp_path / "tracing"), "bpftrace")
    assert out["buffer_size_kb"] == 32768
    assert out["tracing_on"] == 1
    assert out["current_tracer"] == "function"


# --- classify --------------------------------------------------

def _inst(*, name="x", buffer_size_kb=7000, tracing_on=0,
          current_tracer="nop"):
    return {"name": name,
            "buffer_size_kb": buffer_size_kb,
            "tracing_on": tracing_on,
            "current_tracer": current_tracer}


def test_classify_unknown_no_root():
    v = mod.classify([], 1, False, False)
    assert v["verdict"] == "unknown"


def test_classify_requires_root_unreadable():
    v = mod.classify([], 1, True, True)
    assert v["verdict"] == "requires_root"


def test_classify_clean_no_instances():
    v = mod.classify([], 1, True, False)
    assert v["verdict"] == "instances_clean"


def test_classify_clean_idle_instances():
    v = mod.classify(
        [_inst(name="bpftrace", tracing_on=0)],
        4, True, False)
    assert v["verdict"] == "instances_clean"


def test_classify_orphan_burning_ram():
    # 100 MiB × 4 cpus = 400 MiB total (> 256 MiB threshold)
    v = mod.classify(
        [_inst(name="bpftrace", tracing_on=1,
               buffer_size_kb=100_000)],
        4, True, False)
    assert v["verdict"] == "orphan_instance_burning_ram"


def test_classify_armed_but_under_threshold():
    # 4 MiB × 1 cpu = 4 MiB < threshold but tracer armed
    v = mod.classify(
        [_inst(name="bpftrace", tracing_on=1,
               buffer_size_kb=4096,
               current_tracer="function")],
        1, True, False)
    assert v["verdict"] == "instance_left_armed"


def test_classify_armed_with_nop_is_idle():
    # tracing_on=1 + current_tracer=nop is harmless
    v = mod.classify(
        [_inst(name="x", tracing_on=1,
               current_tracer="nop")],
        1, True, False)
    assert v["verdict"] == "instances_clean"


def test_classify_many_instances():
    v = mod.classify(
        [_inst(name=f"i{i}") for i in range(5)],
        1, True, False)
    assert v["verdict"] == "many_instances"


# Priority : orphan > armed > many_instances
def test_priority_orphan_over_armed():
    v = mod.classify([
        _inst(name="a", tracing_on=1,
              buffer_size_kb=100_000,
              current_tracer="function"),
        _inst(name="b", tracing_on=1,
              current_tracer="function"),
    ], 4, True, False)
    assert v["verdict"] == "orphan_instance_burning_ram"


def test_priority_armed_over_many():
    v = mod.classify(
        [_inst(name=f"i{i}",
               tracing_on=(1 if i == 0 else 0),
               current_tracer=("function"
                               if i == 0 else "nop"))
         for i in range(5)],
        1, True, False)
    assert v["verdict"] == "instance_left_armed"


# --- status integration ----------------------------------------

def test_status_unknown(tmp_path):
    out = mod.status(None, str(tmp_path / "nope"))
    assert out["verdict"]["verdict"] == "unknown"


def test_status_clean_synthetic(tmp_path):
    r = _mk_root(tmp_path, {"bpftrace": {
        "tracing_on": 0, "current_tracer": "nop"}})
    out = mod.status(None, r)
    assert out["verdict"]["verdict"] == "instances_clean"
    assert out["instance_count"] == 1


def test_status_orphan_synthetic(tmp_path):
    # huge buffer × nr_cpus → orphan
    r = _mk_root(tmp_path, {"perfetto": {
        "buffer_size_kb": 500_000,
        "tracing_on": 1,
        "current_tracer": "function"}})
    out = mod.status(None, r)
    assert (out["verdict"]["verdict"]
            == "orphan_instance_burning_ram")
    assert out["ok"] is False
