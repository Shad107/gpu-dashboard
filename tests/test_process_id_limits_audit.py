"""Tests for modules/process_id_limits_audit.py — R&D #73.1."""
from __future__ import annotations

import pytest

from gpu_dashboard.modules import process_id_limits_audit as mod


# --- count_active_pids -----------------------------------------

def test_count_missing(tmp_path):
    assert mod.count_active_pids(str(tmp_path / "nope")) == 0


def test_count_active(tmp_path):
    for pid in (1, 100, 200, 300):
        (tmp_path / str(pid)).mkdir()
    (tmp_path / "self").mkdir()
    (tmp_path / "cpuinfo").write_text("")
    assert mod.count_active_pids(str(tmp_path)) == 4


# --- classify ---------------------------------------------------

def test_classify_unknown_all_none():
    v = mod.classify(None, None, None, 0)
    assert v["verdict"] == "unknown"


def test_classify_ok():
    v = mod.classify(4194304, 247180, 1048576, 367)
    assert v["verdict"] == "ok"


def test_classify_pid_exhaustion():
    v = mod.classify(10000, 200000, 1048576, 9000)
    assert v["verdict"] == "pid_exhaustion_imminent"


def test_classify_threads_max_too_low():
    v = mod.classify(4194304, 50000, 1048576, 100)
    assert v["verdict"] == "threads_max_too_low"


def test_classify_pid_max_legacy():
    v = mod.classify(32768, 200000, 1048576, 100)
    assert v["verdict"] == "pid_max_legacy_32k"


def test_classify_max_map_count_too_low():
    v = mod.classify(4194304, 200000, 65000, 100)
    assert v["verdict"] == "max_map_count_too_low"


# Priority : pid_exhaustion > threads_max > pid_max_legacy >
# max_map_count
def test_priority_pid_exhaustion_over_threads():
    v = mod.classify(10000, 50000, 65000, 9000)
    assert v["verdict"] == "pid_exhaustion_imminent"


def test_priority_threads_over_pid_legacy():
    v = mod.classify(32768, 50000, 65000, 100)
    assert v["verdict"] == "threads_max_too_low"


def test_priority_pid_legacy_over_max_map():
    v = mod.classify(32768, 200000, 65000, 100)
    assert v["verdict"] == "pid_max_legacy_32k"


# --- status integration -----------------------------------------

def test_status_unknown(tmp_path):
    out = mod.status(None,
                          str(tmp_path / "no_pid"),
                          str(tmp_path / "no_threads"),
                          str(tmp_path / "no_mmc"),
                          str(tmp_path / "no_proc"))
    assert out["ok"] is False
    assert out["verdict"]["verdict"] == "unknown"


def test_status_ok_synthetic(tmp_path):
    pm = tmp_path / "pid_max"; pm.write_text("4194304\n")
    tm = tmp_path / "threads-max"; tm.write_text("200000\n")
    mmc = tmp_path / "max_map_count"; mmc.write_text("1048576\n")
    proc_root = tmp_path / "proc"; proc_root.mkdir()
    for pid in (1, 100, 200):
        (proc_root / str(pid)).mkdir()
    out = mod.status(None, str(pm), str(tm), str(mmc),
                          str(proc_root))
    assert out["ok"] is True
    assert out["pid_max"] == 4194304
    assert out["active_pids"] == 3
    assert out["verdict"]["verdict"] == "ok"


def test_status_legacy_pid_max(tmp_path):
    pm = tmp_path / "pid_max"; pm.write_text("32768\n")
    tm = tmp_path / "threads-max"; tm.write_text("200000\n")
    mmc = tmp_path / "max_map_count"; mmc.write_text("1048576\n")
    proc_root = tmp_path / "proc"; proc_root.mkdir()
    out = mod.status(None, str(pm), str(tm), str(mmc),
                          str(proc_root))
    assert out["verdict"]["verdict"] == "pid_max_legacy_32k"
