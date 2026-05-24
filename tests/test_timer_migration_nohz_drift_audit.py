"""Tests for modules/timer_migration_nohz_drift_audit.py
R&D #88.4."""
from __future__ import annotations

import pytest

from gpu_dashboard.modules import (
    timer_migration_nohz_drift_audit as mod)


def _mk_proc_sys(tmp_path, *, tm="1"):
    d = tmp_path / "sys" / "kernel"
    d.mkdir(parents=True, exist_ok=True)
    if tm is not None:
        (d / "timer_migration").write_text(tm + "\n")
    return str(tmp_path / "sys")


def _mk_sys_cpu(tmp_path, *, nohz_full="(null)", isolated="",
                 online="0-11"):
    d = tmp_path / "syscpu"
    d.mkdir(parents=True, exist_ok=True)
    (d / "nohz_full").write_text(nohz_full + "\n")
    (d / "isolated").write_text(isolated + "\n")
    (d / "online").write_text(online + "\n")
    return str(d)


def _mk_cmdline(tmp_path, text):
    p = tmp_path / "cmdline"
    p.write_text(text + "\n")
    return str(p)


# --- parse_cpu_list --------------------------------------------

def test_parse_empty():
    assert mod.parse_cpu_list("") == set()
    assert mod.parse_cpu_list(None) == set()


def test_parse_null_literal():
    # /sys/devices/system/cpu/nohz_full shows '(null)' when
    # nohz_full is unset on this kernel.
    assert mod.parse_cpu_list("(null)") == set()


def test_parse_range_and_single():
    assert mod.parse_cpu_list("0-3,8,10-11") == {
        0, 1, 2, 3, 8, 10, 11}


def test_parse_garbage_skipped():
    assert mod.parse_cpu_list("0-3,zz,5") == {0, 1, 2, 3, 5}


# --- read_state ------------------------------------------------

def test_read_state_missing(tmp_path):
    proc_sys = str(tmp_path / "nope_sys")
    sys_cpu = str(tmp_path / "nope_syscpu")
    cmdline = str(tmp_path / "nope_cmdline")
    state = mod.read_state(proc_sys, sys_cpu, cmdline)
    assert state["timer_migration"] is None
    assert state["nohz_full"] == set()


def test_read_state_full(tmp_path):
    proc_sys = _mk_proc_sys(tmp_path, tm="0")
    sys_cpu = _mk_sys_cpu(tmp_path, nohz_full="4-7",
                              isolated="4-7", online="0-7")
    cmdline = _mk_cmdline(
        tmp_path, "ro nohz_full=4-7 rcu_nocbs=4-7")
    state = mod.read_state(proc_sys, sys_cpu, cmdline)
    assert state["timer_migration"] == 0
    assert state["nohz_full"] == {4, 5, 6, 7}
    assert state["isolated"] == {4, 5, 6, 7}
    assert state["cmdline_nohz_full"] == {4, 5, 6, 7}
    assert state["cmdline_rcu_nocbs"] == {4, 5, 6, 7}


# --- classify --------------------------------------------------

def test_classify_unknown_no_tm(tmp_path):
    state = {
        "timer_migration": None,
        "tm_readable": False,
        "nohz_full": set(),
        "isolated": set(),
        "online": set(),
        "cmdline_nohz_full": set(),
        "cmdline_rcu_nocbs": set(),
    }
    v = mod.classify(state)
    assert v["verdict"] == "requires_root"


def test_classify_unknown_old_kernel():
    state = {
        "timer_migration": None,
        "tm_readable": True,
        "nohz_full": set(),
        "isolated": set(),
        "online": set(),
        "cmdline_nohz_full": set(),
        "cmdline_rcu_nocbs": set(),
    }
    # tm_readable=True but value is None means parse failed
    # — treat as unknown.
    v = mod.classify(state)
    assert v["verdict"] == "unknown"


def test_classify_nohz_without_tm_off():
    state = {
        "timer_migration": 1,
        "tm_readable": True,
        "nohz_full": {4, 5, 6, 7},
        "isolated": {4, 5, 6, 7},
        "online": {0, 1, 2, 3, 4, 5, 6, 7},
        "cmdline_nohz_full": {4, 5, 6, 7},
        "cmdline_rcu_nocbs": {4, 5, 6, 7},
    }
    v = mod.classify(state)
    assert v["verdict"] == "nohz_full_without_timer_migration_off"


def test_classify_tm_off_no_isolation():
    state = {
        "timer_migration": 0,
        "tm_readable": True,
        "nohz_full": set(),
        "isolated": set(),
        "online": {0, 1, 2, 3},
        "cmdline_nohz_full": set(),
        "cmdline_rcu_nocbs": set(),
    }
    v = mod.classify(state)
    assert v["verdict"] == "timer_migration_off_no_isolation"


def test_classify_rcu_nocbs_mismatch():
    state = {
        "timer_migration": 0,
        "tm_readable": True,
        "nohz_full": {4, 5, 6, 7},
        "isolated": {4, 5, 6, 7},
        "online": {0, 1, 2, 3, 4, 5, 6, 7},
        "cmdline_nohz_full": {4, 5, 6, 7},
        "cmdline_rcu_nocbs": {4, 5, 6},  # different
    }
    v = mod.classify(state)
    assert v["verdict"] == "rcu_nocbs_mismatch_nohz_full"


def test_classify_aligned():
    state = {
        "timer_migration": 1,
        "tm_readable": True,
        "nohz_full": set(),
        "isolated": set(),
        "online": {0, 1, 2, 3},
        "cmdline_nohz_full": set(),
        "cmdline_rcu_nocbs": set(),
    }
    v = mod.classify(state)
    assert v["verdict"] == "aligned"


def test_classify_aligned_isolated_no_nohz():
    state = {
        "timer_migration": 0,
        "tm_readable": True,
        "nohz_full": set(),
        "isolated": {4, 5, 6, 7},
        "online": {0, 1, 2, 3, 4, 5, 6, 7},
        "cmdline_nohz_full": set(),
        "cmdline_rcu_nocbs": set(),
    }
    v = mod.classify(state)
    # isolated present without nohz_full but tm=0 → aligned
    assert v["verdict"] == "aligned"


# Priority : err > tm_off_no_iso > rcu_nocbs_mismatch
def test_priority_err_over_warn():
    state = {
        "timer_migration": 1,
        "tm_readable": True,
        "nohz_full": {4, 5},
        "isolated": set(),
        "online": {0, 1, 2, 3, 4, 5},
        "cmdline_nohz_full": {4, 5},
        "cmdline_rcu_nocbs": {6, 7},
    }
    v = mod.classify(state)
    assert v["verdict"] == "nohz_full_without_timer_migration_off"


def test_priority_tm_off_over_rcu_mismatch():
    state = {
        "timer_migration": 0,
        "tm_readable": True,
        "nohz_full": set(),
        "isolated": set(),
        "online": {0, 1, 2, 3},
        "cmdline_nohz_full": set(),
        "cmdline_rcu_nocbs": set(),
    }
    v = mod.classify(state)
    assert v["verdict"] == "timer_migration_off_no_isolation"


# --- status integration ----------------------------------------

def test_status_aligned_synthetic(tmp_path):
    proc_sys = _mk_proc_sys(tmp_path, tm="1")
    sys_cpu = _mk_sys_cpu(tmp_path)
    cmdline = _mk_cmdline(tmp_path, "ro")
    out = mod.status(None, proc_sys, sys_cpu, cmdline)
    assert out["verdict"]["verdict"] == "aligned"
    assert out["ok"] is True
    assert out["timer_migration"] == 1


def test_status_err_synthetic(tmp_path):
    proc_sys = _mk_proc_sys(tmp_path, tm="1")
    sys_cpu = _mk_sys_cpu(tmp_path, nohz_full="4-7",
                              isolated="4-7")
    cmdline = _mk_cmdline(tmp_path,
                              "ro nohz_full=4-7 rcu_nocbs=4-7")
    out = mod.status(None, proc_sys, sys_cpu, cmdline)
    assert (out["verdict"]["verdict"]
            == "nohz_full_without_timer_migration_off")
    assert out["ok"] is False


def test_status_requires_root_synthetic(tmp_path):
    proc_sys = str(tmp_path / "nope_sys")
    sys_cpu = _mk_sys_cpu(tmp_path)
    cmdline = _mk_cmdline(tmp_path, "ro")
    out = mod.status(None, proc_sys, sys_cpu, cmdline)
    assert out["verdict"]["verdict"] == "requires_root"
    assert out["ok"] is False
