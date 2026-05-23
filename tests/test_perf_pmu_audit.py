"""Tests for modules/perf_pmu_audit.py — R&D #51.3."""
from __future__ import annotations

import pytest

from gpu_dashboard.modules import perf_pmu_audit as mod


def _mk_pmu(root, name, type_=1, nr_addr_filters=0,
              cpumask=None, events=None, format_fields=None):
    d = root / name
    d.mkdir(parents=True, exist_ok=True)
    (d / "type").write_text(str(type_) + "\n")
    if nr_addr_filters is not None:
        (d / "nr_addr_filters").write_text(
            str(nr_addr_filters) + "\n")
    if cpumask is not None:
        (d / "cpumask").write_text(cpumask + "\n")
    if events:
        ev = d / "events"
        ev.mkdir()
        for e in events:
            (ev / e).write_text("event=0x00\n")
    if format_fields:
        fmt = d / "format"
        fmt.mkdir()
        for f in format_fields:
            (fmt / f).write_text("config:0-7\n")


# --- classify_pmu ------------------------------------------------

def test_classify_pmu_kernel():
    for n in ("software", "tracepoint", "kprobe",
                "uprobe", "breakpoint"):
        assert mod.classify_pmu(n) == "kernel"


def test_classify_pmu_cpu_hw():
    assert mod.classify_pmu("cpu") == "cpu_hardware"
    assert mod.classify_pmu("cpu_core") == "cpu_hardware"
    assert mod.classify_pmu("cpu_atom") == "cpu_hardware"


def test_classify_pmu_uncore():
    assert mod.classify_pmu("uncore_imc_0") == "memory_controller"
    assert mod.classify_pmu("uncore_cha_3") == "cache_agent"
    assert mod.classify_pmu("uncore_irp_2") == "uncore_io"
    assert mod.classify_pmu("uncore_m2m") == "uncore_other"


def test_classify_pmu_special():
    assert mod.classify_pmu("msr") == "msr"
    assert mod.classify_pmu("power") == "rapl_energy"
    assert mod.classify_pmu("intel_pt") == "intel_pt"
    assert mod.classify_pmu("nvidia") == "gpu"


def test_classify_pmu_other():
    assert mod.classify_pmu("weird_thing") == "other"


# --- list_pmus ---------------------------------------------------

def test_list_pmus_missing(tmp_path):
    assert mod.list_pmus(str(tmp_path / "nope")) == []


def test_list_pmus_empty(tmp_path):
    assert mod.list_pmus(str(tmp_path)) == []


def test_list_pmus_basic(tmp_path):
    _mk_pmu(tmp_path, "software", type_=1)
    _mk_pmu(tmp_path, "msr", type_=10,
              events=["bus-cycles", "cpu-cycles"])
    out = mod.list_pmus(str(tmp_path))
    assert len(out) == 2
    msr = next(p for p in out if p["name"] == "msr")
    assert msr["type"] == 10
    assert msr["event_count"] == 2
    assert "bus-cycles" in msr["events"]
    assert msr["category"] == "msr"


# --- classify ----------------------------------------------------

def test_classify_no_pmu():
    v = mod.classify([])
    assert v["verdict"] == "no_pmu"


def test_classify_inventory():
    v = mod.classify([{"name": "software", "category": "kernel"},
                       {"name": "msr", "category": "msr"}])
    assert v["verdict"] == "pmu_inventory"
    assert "kernel=1" in v["reason"]
    assert "msr=1" in v["reason"]


# --- status integration ------------------------------------------

def test_status_no_pmu(monkeypatch, tmp_path):
    monkeypatch.setattr(mod, "_SYS_EVENT_SOURCE",
                        str(tmp_path / "nope"))
    out = mod.status()
    assert out["ok"] is False
    assert out["verdict"]["verdict"] == "no_pmu"


def test_status_with_pmus(monkeypatch, tmp_path):
    syse = tmp_path / "e"
    _mk_pmu(syse, "software", type_=1)
    _mk_pmu(syse, "cpu_core", type_=8, events=["cycles"])
    _mk_pmu(syse, "uncore_imc_0", type_=18,
              events=["cas_count_read", "cas_count_write"])
    monkeypatch.setattr(mod, "_SYS_EVENT_SOURCE", str(syse))
    out = mod.status()
    assert out["ok"] is True
    assert out["pmu_count"] == 3
    assert out["verdict"]["verdict"] == "pmu_inventory"
