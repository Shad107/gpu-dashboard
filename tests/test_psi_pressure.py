"""Tests for modules/psi_pressure.py — R&D #32.1 PSI pressure correlator."""
from __future__ import annotations

from pathlib import Path

import pytest

from gpu_dashboard.modules import psi_pressure


_SAMPLE_CPU = """\
some avg10=15.17 avg60=8.08 avg300=2.16 total=1712390431
full avg10=0.00 avg60=0.00 avg300=0.00 total=0
"""

_SAMPLE_MEM = """\
some avg10=0.00 avg60=0.00 avg300=0.00 total=274665522
full avg10=0.00 avg60=0.00 avg300=0.00 total=255139631
"""

_SAMPLE_IO_LIGHT = """\
some avg10=0.50 avg60=0.10 avg300=0.05 total=885717007
full avg10=0.30 avg60=0.05 avg300=0.01 total=802868802
"""

_SAMPLE_IO_HEAVY = """\
some avg10=42.10 avg60=20.50 avg300=10.10 total=1
full avg10=15.30 avg60=8.50 avg300=4.10 total=1
"""


def _mk_pressure(root: Path, **files):
    root.mkdir(parents=True, exist_ok=True)
    for k, v in files.items():
        (root / k).write_text(v)


# --- parse_psi ---------------------------------------------------

def test_parse_psi_some_and_full():
    p = psi_pressure.parse_psi(_SAMPLE_CPU)
    assert p["some"]["avg10"] == 15.17
    assert p["some"]["avg60"] == 8.08
    assert p["some"]["avg300"] == 2.16
    assert p["some"]["total_us"] == 1712390431
    assert p["full"]["avg10"] == 0.0


def test_parse_psi_empty_returns_empty():
    assert psi_pressure.parse_psi("") == {}


def test_parse_psi_only_some_line():
    p = psi_pressure.parse_psi("some avg10=2.0 avg60=1.0 avg300=0.5 total=100\n")
    assert "some" in p
    assert "full" not in p


def test_parse_psi_malformed_skipped():
    p = psi_pressure.parse_psi("garbage line\nsome avg10=5.0 avg60=2.0 avg300=1.0 total=999\n")
    assert p["some"]["avg10"] == 5.0


# --- read_resource ----------------------------------------------

def test_read_resource_returns_parsed(tmp_path):
    _mk_pressure(tmp_path, cpu=_SAMPLE_CPU)
    r = psi_pressure.read_resource(str(tmp_path), "cpu")
    assert r["some"]["avg10"] == 15.17


def test_read_resource_missing_returns_none(tmp_path):
    assert psi_pressure.read_resource(str(tmp_path), "memory") is None


# --- classify ---------------------------------------------------

def test_classify_ok_low_pressure():
    psi = {"some": {"avg10": 0.5, "avg60": 0.1, "avg300": 0.0, "total_us": 1},
           "full": {"avg10": 0.0, "avg60": 0.0, "avg300": 0.0, "total_us": 0}}
    v = psi_pressure.classify("cpu", psi)
    assert v["verdict"] == "ok"


def test_classify_elevated_some_high():
    # Live rig: some=15.17, full=0 → elevated, not throttled
    psi = {"some": {"avg10": 15.17, "avg60": 8.08, "avg300": 2.16,
                      "total_us": 1712390431},
           "full": {"avg10": 0.0, "avg60": 0.0, "avg300": 0.0, "total_us": 0}}
    v = psi_pressure.classify("cpu", psi)
    assert v["verdict"] == "elevated"
    assert "15" in v["reason"]
    assert "CPUAffinity" in v["recommendation"] or "taskset" in v["recommendation"]


def test_classify_throttled_full_high():
    psi = {"some": {"avg10": 50.0, "avg60": 25.0, "avg300": 12.0,
                      "total_us": 1},
           "full": {"avg10": 20.0, "avg60": 10.0, "avg300": 5.0,
                      "total_us": 1}}
    v = psi_pressure.classify("memory", psi)
    assert v["verdict"] == "throttled"
    # Memory throttled → recommend the cause→symptom chain
    assert ("swappiness" in v["recommendation"] or
            "rlimit" in v["recommendation"].lower() or
            "vm_sysctl" in v["recommendation"].lower())


def test_classify_throttled_io():
    psi = {"some": {"avg10": 42.0, "avg60": 20.0, "avg300": 10.0,
                      "total_us": 1},
           "full": {"avg10": 15.0, "avg60": 8.0, "avg300": 4.0,
                      "total_us": 1}}
    v = psi_pressure.classify("io", psi)
    assert v["verdict"] == "throttled"
    # IO throttled → NVMe scheduler #30.3 hint
    assert "nvme" in v["recommendation"].lower() or "iosched" in v["recommendation"].lower()


def test_classify_missing_resource():
    v = psi_pressure.classify("cpu", None)
    assert v["verdict"] == "missing"
    assert "PSI" in v["reason"] or "CONFIG_PSI" in v["reason"]


def test_classify_uses_avg10_not_avg60():
    # avg60 high but avg10 low → recent peace, verdict should be ok
    psi = {"some": {"avg10": 1.0, "avg60": 50.0, "avg300": 25.0,
                      "total_us": 1},
           "full": {"avg10": 0.0, "avg60": 10.0, "avg300": 5.0,
                      "total_us": 1}}
    v = psi_pressure.classify("cpu", psi)
    assert v["verdict"] == "ok"


# --- status -----------------------------------------------------

def test_status_no_pressure_dir(tmp_path, monkeypatch):
    monkeypatch.setattr(psi_pressure, "_PSI_ROOT", str(tmp_path / "absent"))
    s = psi_pressure.status()
    assert s["ok"] is False
    assert s["error"] == "psi_unavailable"


def test_status_full_live_layout(tmp_path, monkeypatch):
    _mk_pressure(tmp_path, cpu=_SAMPLE_CPU, memory=_SAMPLE_MEM,
                 io=_SAMPLE_IO_LIGHT)
    monkeypatch.setattr(psi_pressure, "_PSI_ROOT", str(tmp_path))
    s = psi_pressure.status()
    assert s["ok"] is True
    assert s["worst_verdict"] == "elevated"  # CPU some.avg10=15.17
    # All three resources present
    names = {r["resource"] for r in s["resources"]}
    assert names == {"cpu", "memory", "io"}


def test_status_picks_worst_across(tmp_path, monkeypatch):
    _mk_pressure(tmp_path,
                 cpu="some avg10=0.5 avg60=0.1 avg300=0.05 total=1\n"
                     "full avg10=0.0 avg60=0.0 avg300=0.0 total=0\n",
                 memory="some avg10=0.0 avg60=0.0 avg300=0.0 total=1\n"
                        "full avg10=0.0 avg60=0.0 avg300=0.0 total=0\n",
                 io=_SAMPLE_IO_HEAVY)
    monkeypatch.setattr(psi_pressure, "_PSI_ROOT", str(tmp_path))
    s = psi_pressure.status()
    assert s["worst_verdict"] == "throttled"


def test_status_partial_psi(tmp_path, monkeypatch):
    # Some old kernels only expose cpu pressure
    _mk_pressure(tmp_path, cpu=_SAMPLE_CPU)
    monkeypatch.setattr(psi_pressure, "_PSI_ROOT", str(tmp_path))
    s = psi_pressure.status()
    assert s["ok"] is True
    resources = {r["resource"]: r for r in s["resources"]}
    assert "cpu" in resources
    assert resources["memory"]["verdict"]["verdict"] == "missing"


def test_status_exposes_total_microseconds(tmp_path, monkeypatch):
    _mk_pressure(tmp_path, cpu=_SAMPLE_CPU, memory=_SAMPLE_MEM,
                 io=_SAMPLE_IO_LIGHT)
    monkeypatch.setattr(psi_pressure, "_PSI_ROOT", str(tmp_path))
    s = psi_pressure.status()
    cpu = next(r for r in s["resources"] if r["resource"] == "cpu")
    assert cpu["psi"]["some"]["total_us"] == 1712390431
