"""Tests for modules/psi_irq_full_audit.py R&D #98.1."""
from __future__ import annotations

import pytest

from gpu_dashboard.modules import psi_irq_full_audit as mod


_QUIET = (
    "some avg10=0.00 avg60=0.00 avg300=0.00 total=1\n"
    "full avg10=0.00 avg60=0.00 avg300=0.00 total=0\n")


# --- parse_pressure -------------------------------------------

def test_parse_empty():
    out = mod.parse_pressure("")
    assert out == {"some": {}, "full": {}}


def test_parse_quiet():
    out = mod.parse_pressure(_QUIET)
    assert out["full"]["a60"] == 0.0
    assert out["some"]["total"] == 1


def test_parse_only_some():
    out = mod.parse_pressure(
        "some avg10=1.23 avg60=4.56 avg300=7.89 total=42\n")
    assert out["some"]["a10"] == 1.23
    assert out["full"] == {}


# --- classify -------------------------------------------------

def _full(*, a10=0.0, a60=0.0, a300=0.0, total=0):
    return {"a10": a10, "a60": a60,
            "a300": a300, "total": total}


def test_classify_unknown_no_cpu():
    v = mod.classify(False, False, {}, False, False, {})
    assert v["verdict"] == "unknown"


def test_classify_requires_root_cpu():
    v = mod.classify(True, False, {}, False, False, {})
    assert v["verdict"] == "requires_root"


def test_classify_requires_root_irq_unreadable():
    v = mod.classify(True, True, _full(),
                          True, False, {})
    assert v["verdict"] == "requires_root"


def test_classify_ok():
    v = mod.classify(True, True, _full(),
                          True, True,
                          {"some": {}, "full": _full()})
    assert v["verdict"] == "ok"


def test_classify_irq_full_stall():
    v = mod.classify(True, True, _full(),
                          True, True,
                          {"some": {}, "full": _full(a60=25.0)})
    assert v["verdict"] == "irq_full_stall"


def test_classify_cpu_full_stall():
    v = mod.classify(True, True, _full(a60=15.0),
                          True, True,
                          {"some": {}, "full": _full()})
    assert v["verdict"] == "cpu_full_stall"


def test_classify_psi_irq_absent():
    v = mod.classify(True, True, _full(),
                          False, True, {})
    assert v["verdict"] == "psi_irq_absent"


# Priority : irq_full > cpu_full > psi_irq_absent
def test_priority_irq_over_cpu():
    v = mod.classify(True, True, _full(a60=15.0),
                          True, True,
                          {"some": {}, "full": _full(a60=25.0)})
    assert v["verdict"] == "irq_full_stall"


def test_priority_cpu_over_absent():
    v = mod.classify(True, True, _full(a60=15.0),
                          False, True, {})
    assert v["verdict"] == "cpu_full_stall"


# --- status integration ---------------------------------------

def test_status_unknown(tmp_path):
    out = mod.status(None, str(tmp_path / "no_pressure"))
    assert out["verdict"]["verdict"] == "unknown"


def test_status_ok_synthetic(tmp_path):
    d = tmp_path / "pressure"
    d.mkdir()
    (d / "cpu").write_text(_QUIET)
    (d / "irq").write_text(_QUIET)
    out = mod.status(None, str(d))
    assert out["verdict"]["verdict"] == "ok"
    assert out["irq_present"] is True


def test_status_psi_irq_absent_synthetic(tmp_path):
    # cpu file exists, irq doesn't — this is the Ubuntu
    # generic-kernel real-world case.
    d = tmp_path / "pressure"
    d.mkdir()
    (d / "cpu").write_text(_QUIET)
    out = mod.status(None, str(d))
    assert out["verdict"]["verdict"] == "psi_irq_absent"
    assert out["ok"] is False
