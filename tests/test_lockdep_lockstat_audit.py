"""Tests for modules/lockdep_lockstat_audit.py — R&D #94.4."""
from __future__ import annotations

import pytest

from gpu_dashboard.modules import lockdep_lockstat_audit as mod


def _mk_proc(tmp_path, *, lockdep_stats=None, lockdep=None,
              lock_stat=None):
    d = tmp_path / "proc"
    d.mkdir(parents=True, exist_ok=True)
    if lockdep_stats is not None:
        (d / "lockdep_stats").write_text(lockdep_stats)
    if lockdep is not None:
        (d / "lockdep").write_text(lockdep)
    if lock_stat is not None:
        (d / "lock_stat").write_text(lock_stat)
    return str(d)


# --- lockdep_files_present -------------------------------------

def test_files_present_none(tmp_path):
    p = _mk_proc(tmp_path)
    assert mod.lockdep_files_present(p) is False


def test_files_present_lockdep_stats(tmp_path):
    p = _mk_proc(tmp_path, lockdep_stats="debug_locks: 1\n")
    assert mod.lockdep_files_present(p) is True


def test_files_present_lock_stat(tmp_path):
    p = _mk_proc(tmp_path, lock_stat="some lock stats\n")
    assert mod.lockdep_files_present(p) is True


# --- parse_lockdep_dead ----------------------------------------

def test_parse_dead_empty():
    assert mod.parse_lockdep_dead("") is False


def test_parse_dead_healthy():
    text = (" lock-classes:                          1234\n"
            " debug_locks:                           1\n")
    assert mod.parse_lockdep_dead(text) is False


def test_parse_dead_debug_locks_zero():
    text = " debug_locks:                           0\n"
    assert mod.parse_lockdep_dead(text) is True


def test_parse_dead_max_bug():
    text = " lock-classes:                          1234\n" \
           "BUG: MAX_LOCKDEP_ENTRIES too low!\n"
    assert mod.parse_lockdep_dead(text) is True


def test_parse_dead_lock_chains_bug():
    text = "BUG: MAX_LOCKDEP_CHAINS_BITS too low\n"
    assert mod.parse_lockdep_dead(text) is True


# --- classify --------------------------------------------------

def test_classify_unknown_no_files():
    v = mod.classify(False, None)
    assert v["verdict"] == "unknown"


def test_classify_requires_root_unreadable():
    v = mod.classify(True, None)
    assert v["verdict"] == "requires_root"


def test_classify_lockdep_dead():
    v = mod.classify(True, " debug_locks: 0\n")
    assert v["verdict"] == "lockdep_dead"


def test_classify_enabled_in_prod():
    v = mod.classify(True, " debug_locks: 1\n")
    assert v["verdict"] == "lockdep_enabled_in_prod"


# Priority : dead > enabled
def test_priority_dead_over_enabled():
    v = mod.classify(
        True,
        " lock-classes: 1234\n debug_locks: 0\n")
    assert v["verdict"] == "lockdep_dead"


# --- status integration ----------------------------------------

def test_status_unknown(tmp_path):
    p = _mk_proc(tmp_path)
    out = mod.status(None, p)
    assert out["verdict"]["verdict"] == "unknown"
    assert out["lockdep_present"] is False


def test_status_enabled_in_prod_synthetic(tmp_path):
    p = _mk_proc(tmp_path,
                       lockdep_stats=" debug_locks: 1\n")
    out = mod.status(None, p)
    assert (out["verdict"]["verdict"]
            == "lockdep_enabled_in_prod")
    assert out["lockdep_present"] is True
    assert out["lockdep_dead"] is False


def test_status_lockdep_dead_synthetic(tmp_path):
    p = _mk_proc(tmp_path,
                       lockdep_stats=" debug_locks: 0\n")
    out = mod.status(None, p)
    assert out["verdict"]["verdict"] == "lockdep_dead"
    assert out["lockdep_dead"] is True
