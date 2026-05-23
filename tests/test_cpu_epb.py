"""Tests for modules/cpu_epb.py — R&D #42.4."""
from __future__ import annotations

from pathlib import Path

import pytest

from gpu_dashboard.modules import cpu_epb as mod


def _mk_cpu(root: Path, n: int, *, epb: int | None = None):
    cdir = root / f"cpu{n}"
    pdir = cdir / "power"
    pdir.mkdir(parents=True, exist_ok=True)
    if epb is not None:
        (pdir / "energy_perf_bias").write_text(str(epb) + "\n")


# --- epb_label -----------------------------------------------------

def test_epb_label_known():
    assert mod.epb_label(0) == "performance"
    assert mod.epb_label(4) == "balance_performance"
    assert mod.epb_label(6) == "normal"
    assert mod.epb_label(8) == "balance_power"
    assert mod.epb_label(15) == "powersave"


def test_epb_label_raw_passthrough():
    assert mod.epb_label(7) == "raw_7"
    assert mod.epb_label(12) == "raw_12"


# --- list_cpus -----------------------------------------------------

def test_list_cpus_numeric_sort(tmp_path):
    for n in [0, 1, 2, 10, 11]:
        _mk_cpu(tmp_path, n)
    # Decoys
    (tmp_path / "cpuidle").mkdir()
    (tmp_path / "cpufreq").mkdir()
    cpus = mod.list_cpus(str(tmp_path))
    assert cpus == ["cpu0", "cpu1", "cpu2", "cpu10", "cpu11"]


def test_list_cpus_missing(tmp_path):
    assert mod.list_cpus(str(tmp_path / "nope")) == []


# --- read_per_cpu_epb ----------------------------------------------

def test_read_per_cpu_epb_basic(tmp_path):
    _mk_cpu(tmp_path, 0, epb=6)
    _mk_cpu(tmp_path, 1, epb=6)
    _mk_cpu(tmp_path, 2, epb=8)
    out = mod.read_per_cpu_epb(str(tmp_path))
    assert len(out) == 3
    assert out[0] == {"cpu": 0, "epb": 6, "label": "normal"}
    assert out[2] == {"cpu": 2, "epb": 8, "label": "balance_power"}


def test_read_per_cpu_epb_missing_file(tmp_path):
    _mk_cpu(tmp_path, 0)  # power/ dir but no file
    out = mod.read_per_cpu_epb(str(tmp_path))
    assert out[0]["epb"] is None
    assert out[0]["label"] is None


# --- classify ------------------------------------------------------

def test_classify_unknown_when_empty():
    v = mod.classify([])
    assert v["verdict"] == "unknown"


def test_classify_epb_unavailable():
    per_cpu = [{"cpu": i, "epb": None, "label": None}
               for i in range(4)]
    v = mod.classify(per_cpu)
    assert v["verdict"] == "epb_unavailable"
    assert v["recommendation"] == ""


def test_classify_ok_uniform_normal():
    per_cpu = [{"cpu": i, "epb": 6, "label": "normal"}
               for i in range(8)]
    v = mod.classify(per_cpu)
    assert v["verdict"] == "ok"


def test_classify_ok_uniform_perf():
    per_cpu = [{"cpu": i, "epb": 4, "label": "balance_performance"}
               for i in range(8)]
    v = mod.classify(per_cpu)
    assert v["verdict"] == "ok"


def test_classify_uniform_powersave():
    per_cpu = [{"cpu": i, "epb": 8, "label": "balance_power"}
               for i in range(8)]
    v = mod.classify(per_cpu)
    assert v["verdict"] == "uniform_powersave"
    assert "balance_performance" in v["recommendation"]


def test_classify_uniform_powersave_at_15():
    per_cpu = [{"cpu": i, "epb": 15, "label": "powersave"}
               for i in range(8)]
    v = mod.classify(per_cpu)
    assert v["verdict"] == "uniform_powersave"


def test_classify_mixed_across_cpus():
    per_cpu = [
        {"cpu": 0, "epb": 4, "label": "balance_performance"},
        {"cpu": 1, "epb": 4, "label": "balance_performance"},
        {"cpu": 2, "epb": 8, "label": "balance_power"},
        {"cpu": 3, "epb": 8, "label": "balance_power"},
    ]
    v = mod.classify(per_cpu)
    assert v["verdict"] == "mixed_across_cpus"
    assert "balance_performance" in v["reason"]
    assert "balance_power" in v["reason"]


def test_classify_mixed_skips_unavailable_cpus():
    # Some CPUs have EPB, some don't — only consider the ones
    # that do.
    per_cpu = [
        {"cpu": 0, "epb": 6, "label": "normal"},
        {"cpu": 1, "epb": None, "label": None},
        {"cpu": 2, "epb": 6, "label": "normal"},
    ]
    v = mod.classify(per_cpu)
    assert v["verdict"] == "ok"  # uniform across the EPB-exposing CPUs


# --- status integration -------------------------------------------

def test_status_with_isolated_root(monkeypatch, tmp_path):
    for i in range(4):
        _mk_cpu(tmp_path, i, epb=8)
    monkeypatch.setattr(mod, "_SYS_CPU", str(tmp_path))
    out = mod.status()
    assert out["ok"] is True
    assert out["cpu_count"] == 4
    assert out["epb_exposed_count"] == 4
    assert out["verdict"]["verdict"] == "uniform_powersave"


def test_status_no_cpu_dir(monkeypatch, tmp_path):
    monkeypatch.setattr(mod, "_SYS_CPU", str(tmp_path / "nope"))
    out = mod.status()
    assert out["ok"] is False
    assert out["verdict"]["verdict"] == "unknown"


def test_status_epb_unavailable_live_pattern(monkeypatch, tmp_path):
    # CPUs exist, power/ dir exists, but no EPB file — matches the
    # qemu / Snapdragon X / pre-SandyBridge layout.
    for i in range(2):
        _mk_cpu(tmp_path, i)
    monkeypatch.setattr(mod, "_SYS_CPU", str(tmp_path))
    out = mod.status()
    assert out["ok"] is True
    assert out["cpu_count"] == 2
    assert out["epb_exposed_count"] == 0
    assert out["verdict"]["verdict"] == "epb_unavailable"
