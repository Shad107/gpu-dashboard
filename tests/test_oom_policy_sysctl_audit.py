"""Tests for modules/oom_policy_sysctl_audit.py R&D #99.3."""
from __future__ import annotations

import pytest

from gpu_dashboard.modules import oom_policy_sysctl_audit as mod


# --- parse_meminfo_total ---------------------------------------

def test_parse_meminfo_empty():
    assert mod.parse_meminfo_total(None) is None
    assert mod.parse_meminfo_total("") is None


def test_parse_meminfo_basic():
    text = (
        "MemTotal:       32557416 kB\n"
        "MemFree:         8324188 kB\n")
    assert mod.parse_meminfo_total(text) == 32557416


# --- classify --------------------------------------------------

def test_classify_unknown():
    v = mod.classify(False, None, None, None)
    assert v["verdict"] == "unknown"


def test_classify_requires_root():
    v = mod.classify(True, None, None, None)
    assert v["verdict"] == "requires_root"


def test_classify_ok():
    v = mod.classify(True, 0, 0, 1)
    assert v["verdict"] == "ok"


def test_classify_panic_on_oom_err():
    v = mod.classify(True, 1, 0, 1)
    assert v["verdict"] == "panic_on_oom_set"


def test_classify_panic_on_oom_2_err():
    v = mod.classify(True, 2, 0, 1)
    assert v["verdict"] == "panic_on_oom_set"


def test_classify_kill_allocating_warn():
    v = mod.classify(True, 0, 1, 1)
    assert v["verdict"] == "kill_allocating_task"


def test_classify_dump_disabled_accent():
    v = mod.classify(True, 0, 0, 0)
    assert v["verdict"] == "dump_tasks_disabled"


# Priority : panic > kill_allocating > dump_disabled
def test_priority_panic_over_kill():
    v = mod.classify(True, 1, 1, 1)
    assert v["verdict"] == "panic_on_oom_set"


def test_priority_kill_over_dump():
    v = mod.classify(True, 0, 1, 0)
    assert v["verdict"] == "kill_allocating_task"


# --- status integration ----------------------------------------

def test_status_unknown(tmp_path):
    out = mod.status(None,
                       str(tmp_path / "no_vm"),
                       str(tmp_path / "no_meminfo"))
    assert out["verdict"]["verdict"] == "unknown"


def test_status_ok_synthetic(tmp_path):
    d = tmp_path / "vm"
    d.mkdir()
    (d / "panic_on_oom").write_text("0\n")
    (d / "oom_kill_allocating_task").write_text("0\n")
    (d / "oom_dump_tasks").write_text("1\n")
    meminfo = tmp_path / "meminfo"
    meminfo.write_text("MemTotal: 16384 kB\n")
    out = mod.status(None, str(d), str(meminfo))
    assert out["verdict"]["verdict"] == "ok"
    assert out["mem_total_kb"] == 16384


def test_status_panic_synthetic(tmp_path):
    d = tmp_path / "vm"
    d.mkdir()
    (d / "panic_on_oom").write_text("1\n")
    (d / "oom_kill_allocating_task").write_text("0\n")
    (d / "oom_dump_tasks").write_text("1\n")
    meminfo = tmp_path / "meminfo"
    meminfo.write_text("MemTotal: 16384 kB\n")
    out = mod.status(None, str(d), str(meminfo))
    assert out["verdict"]["verdict"] == "panic_on_oom_set"
    assert out["ok"] is False
