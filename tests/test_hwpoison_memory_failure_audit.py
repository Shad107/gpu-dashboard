"""Tests for modules/hwpoison_memory_failure_audit.py
R&D #94.1."""
from __future__ import annotations

import pytest

from gpu_dashboard.modules import (
    hwpoison_memory_failure_audit as mod)


def _mk_meminfo(tmp_path, *, hw_corrupted_kib=0,
                  include_field=True):
    p = tmp_path / "meminfo"
    text = f"MemTotal: 32000000 kB\n"
    if include_field:
        text += f"HardwareCorrupted: {hw_corrupted_kib} kB\n"
    text += "MemFree: 1000000 kB\n"
    p.write_text(text)
    return str(p)


def _mk_vmstat(tmp_path, *, counters=None):
    p = tmp_path / "vmstat"
    text = "oom_kill 0\n"
    if counters:
        for k, v in counters.items():
            text += f"{k} {v}\n"
    p.write_text(text)
    return str(p)


def _mk_edac(tmp_path, *, present=True):
    d = tmp_path / "edac"
    if present:
        (d / "mc0").mkdir(parents=True, exist_ok=True)
    else:
        d.mkdir(parents=True, exist_ok=True)
    return str(d)


# --- parse_hardware_corrupted_kib ------------------------------

def test_parse_hw_corrupted_present():
    text = "HardwareCorrupted:       0 kB\nMemFree: 100 kB\n"
    assert mod.parse_hardware_corrupted_kib(text) == 0


def test_parse_hw_corrupted_nonzero():
    text = "HardwareCorrupted:       4096 kB\n"
    assert mod.parse_hardware_corrupted_kib(text) == 4096


def test_parse_hw_corrupted_absent():
    text = "MemTotal: 100 kB\nMemFree: 50 kB\n"
    assert mod.parse_hardware_corrupted_kib(text) is None


# --- parse_hwpoison_vmstat -------------------------------------

def test_parse_hwpoison_vmstat_empty():
    assert mod.parse_hwpoison_vmstat("") == {}


def test_parse_hwpoison_vmstat_typical():
    text = (
        "oom_kill 0\n"
        "hwpoison_pages_recovered 5\n"
        "hwpoison_pages_failed 1\n"
        "memory_failure_action_required 2\n"
        "unrelated_field 99\n")
    out = mod.parse_hwpoison_vmstat(text)
    assert out["hwpoison_pages_failed"] == 1
    assert out["memory_failure_action_required"] == 2
    assert "unrelated_field" not in out
    assert "oom_kill" not in out


# --- edac_present ----------------------------------------------

def test_edac_present_missing(tmp_path):
    assert mod.edac_present(
        str(tmp_path / "nope")) is False


def test_edac_present_no_mc(tmp_path):
    d = tmp_path / "edac"
    d.mkdir()
    assert mod.edac_present(str(d)) is False


def test_edac_present_with_mc(tmp_path):
    e = _mk_edac(tmp_path)
    assert mod.edac_present(e) is True


# --- classify --------------------------------------------------

def test_classify_unknown():
    v = mod.classify(None, {}, False)
    assert v["verdict"] == "unknown"


def test_classify_edac_no_hwpoison_accent():
    v = mod.classify(None, {}, True)
    assert v["verdict"] == "edac_present_no_hwpoison"


def test_classify_hwpoison_active_err():
    v = mod.classify(4096, {}, False)
    assert v["verdict"] == "hwpoison_active"
    assert v["kib"] == 4096


def test_classify_failed_recoveries_warn():
    v = mod.classify(0, {"hwpoison_pages_failed": 3},
                          False)
    assert v["verdict"] == "hwpoison_failed_recoveries"


def test_classify_ok():
    v = mod.classify(
        0,
        {"hwpoison_pages_recovered": 5,
         "hwpoison_pages_failed": 0},
        False)
    assert v["verdict"] == "ok"


# Priority : hwpoison_active > failed_recoveries
def test_priority_active_over_failed():
    v = mod.classify(4096,
                          {"hwpoison_pages_failed": 5},
                          False)
    assert v["verdict"] == "hwpoison_active"


# --- status integration ----------------------------------------

def test_status_unknown(tmp_path):
    out = mod.status(None,
                       str(tmp_path / "nope_mem"),
                       str(tmp_path / "nope_vm"),
                       str(tmp_path / "nope_edac"))
    assert out["verdict"]["verdict"] == "unknown"


def test_status_ok_synthetic(tmp_path):
    m = _mk_meminfo(tmp_path)
    v = _mk_vmstat(tmp_path)
    e = _mk_edac(tmp_path, present=False)
    out = mod.status(None, m, v, e)
    assert out["verdict"]["verdict"] == "ok"
    assert out["hardware_corrupted_kib"] == 0


def test_status_hwpoison_active_synthetic(tmp_path):
    m = _mk_meminfo(tmp_path, hw_corrupted_kib=8192)
    v = _mk_vmstat(tmp_path)
    e = _mk_edac(tmp_path)
    out = mod.status(None, m, v, e)
    assert out["verdict"]["verdict"] == "hwpoison_active"
    assert out["ok"] is False


def test_status_edac_no_hwpoison(tmp_path):
    m = _mk_meminfo(tmp_path, include_field=False)
    v = _mk_vmstat(tmp_path)
    e = _mk_edac(tmp_path, present=True)
    out = mod.status(None, m, v, e)
    assert (out["verdict"]["verdict"]
            == "edac_present_no_hwpoison")
