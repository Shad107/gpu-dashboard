"""Tests for modules/psi_pressure_audit.py — R&D #53.1."""
from __future__ import annotations

import pytest

from gpu_dashboard.modules import psi_pressure_audit as mod


PSI_QUIET = (
    "some avg10=0.00 avg60=0.00 avg300=0.00 total=57992334\n"
    "full avg10=0.00 avg60=0.00 avg300=0.00 total=0\n"
)

PSI_MEM_HOT = (
    "some avg10=12.34 avg60=8.90 avg300=4.55 total=4123\n"
    "full avg10=10.10 avg60=7.20 avg300=2.50 total=3001\n"
)

PSI_IO_HOT = (
    "some avg10=20.00 avg60=15.00 avg300=7.50 total=99\n"
    "full avg10=10.00 avg60=8.00 avg300=4.00 total=88\n"
)

PSI_CPU_HOT = (
    "some avg10=12.00 avg60=10.00 avg300=6.50 total=12345\n"
    "full avg10=0.00 avg60=0.00 avg300=0.00 total=0\n"
)


# --- parse_pressure ---------------------------------------------

def test_parse_pressure_quiet():
    out = mod.parse_pressure(PSI_QUIET)
    assert "some" in out
    assert "full" in out
    assert out["some"]["avg300"] == 0.00
    assert out["full"]["total"] == 0


def test_parse_pressure_hot():
    out = mod.parse_pressure(PSI_MEM_HOT)
    assert out["full"]["avg300"] == 2.50
    assert out["full"]["total"] == 3001


def test_parse_pressure_empty():
    assert mod.parse_pressure("") == {}
    assert mod.parse_pressure(None) == {}


def test_parse_pressure_garbage():
    assert mod.parse_pressure("garbage\n") == {}


# --- read_pressure ----------------------------------------------

def _mk_pressure(root, **resources):
    root.mkdir(parents=True, exist_ok=True)
    for res, txt in resources.items():
        (root / res).write_text(txt)


def test_read_pressure_missing(tmp_path):
    out = mod.read_pressure(str(tmp_path / "nope"))
    assert out == {"available": False}


def test_read_pressure_present(tmp_path):
    _mk_pressure(tmp_path, cpu=PSI_CPU_HOT, memory=PSI_QUIET,
                   io=PSI_IO_HOT)
    out = mod.read_pressure(str(tmp_path))
    assert out["available"] is True
    assert out["cpu"]["some"]["avg300"] == 6.50
    assert out["io"]["some"]["avg300"] == 7.50


# --- classify ---------------------------------------------------

def _pressure(cpu_some_a300=0.0, mem_full_a300=0.0,
                io_some_a300=0.0):
    return {
        "available": True,
        "cpu": {"some": {"avg10": 0.0, "avg60": 0.0,
                            "avg300": cpu_some_a300, "total": 0}},
        "memory": {"some": {"avg10": 0.0, "avg60": 0.0,
                               "avg300": 0.0, "total": 0},
                     "full": {"avg10": 0.0, "avg60": 0.0,
                                "avg300": mem_full_a300, "total": 0}},
        "io": {"some": {"avg10": 0.0, "avg60": 0.0,
                           "avg300": io_some_a300, "total": 0}},
    }


def test_classify_psi_disabled():
    v = mod.classify({"available": False}, 0)
    assert v["verdict"] == "psi_disabled"


def test_classify_ok():
    v = mod.classify(_pressure(), 1)
    assert v["verdict"] == "ok"


def test_classify_memory_full_stall_high():
    v = mod.classify(_pressure(mem_full_a300=2.5), 1)
    assert v["verdict"] == "memory_full_stall_high"


def test_classify_io_some_stall_high():
    v = mod.classify(_pressure(io_some_a300=7.0), 1)
    assert v["verdict"] == "io_some_stall_high"


def test_classify_cpu_some_stall_elevated():
    v = mod.classify(_pressure(cpu_some_a300=6.5), 1)
    assert v["verdict"] == "cpu_some_stall_elevated"


def test_classify_priority_memory_wins():
    v = mod.classify(_pressure(mem_full_a300=2.5,
                                  io_some_a300=7.0,
                                  cpu_some_a300=6.5), 1)
    assert v["verdict"] == "memory_full_stall_high"


# --- status integration -----------------------------------------

def test_status_unavailable(tmp_path):
    out = mod.status(None, str(tmp_path / "nope"),
                       str(tmp_path / "noss"))
    assert out["ok"] is False
    assert out["verdict"]["verdict"] == "psi_disabled"


def test_status_live_like(tmp_path):
    pressure = tmp_path / "pressure"
    _mk_pressure(pressure, cpu=PSI_QUIET, memory=PSI_QUIET,
                   io=PSI_QUIET)
    ss = tmp_path / "schedstats"
    ss.write_text("0\n")
    out = mod.status(None, str(pressure), str(ss))
    assert out["ok"] is True
    assert out["sched_schedstats"] == 0
    assert out["verdict"]["verdict"] == "ok"
