"""Tests for modules/proc_locks_contention_audit.py — R&D #87.2."""
from __future__ import annotations

import pytest

from gpu_dashboard.modules import proc_locks_contention_audit as mod


def _lock_line(num, *, blocked=False, pid=1234,
                kind="POSIX", access="WRITE",
                inode="08:02:1000"):
    prefix = f"{num}: -> " if blocked else f"{num}: "
    return (f"{prefix}{kind}  ADVISORY  {access} {pid} "
            f"{inode} 0 EOF\n")


def _mk_proc_pid(tmp_path, pid, comm="testproc"):
    d = tmp_path / str(pid)
    d.mkdir(parents=True, exist_ok=True)
    (d / "comm").write_text(comm + "\n")
    return str(d)


# --- parse_locks -----------------------------------------------

def test_parse_empty():
    total, blocked, _ = mod.parse_locks("")
    assert total == 0 and blocked == 0


def test_parse_no_blocked():
    text = (_lock_line(1) + _lock_line(2))
    total, blocked, _ = mod.parse_locks(text)
    assert total == 2 and blocked == 0


def test_parse_with_blocked():
    text = (_lock_line(1)
              + _lock_line(2, blocked=True, pid=2222)
              + _lock_line(3, blocked=True, pid=3333))
    total, blocked, lines = mod.parse_locks(text)
    assert total == 3
    assert blocked == 2
    assert all("->" in line for line in lines)


def test_parse_skips_garbage():
    text = ("garbage line\n"
              + _lock_line(1))
    total, _, _ = mod.parse_locks(text)
    assert total == 1


# --- _extract_pids ---------------------------------------------

def test_extract_pids():
    lines = [
        _lock_line(1, blocked=True, pid=1234).strip(),
        _lock_line(2, blocked=True, pid=5678).strip(),
    ]
    pids = mod._extract_pids(lines)
    assert pids == {1234, 5678}


def test_extract_pids_garbage_skipped():
    pids = mod._extract_pids(["1: -> something"])
    assert pids == set()


# --- classify --------------------------------------------------

def test_classify_unknown():
    v = mod.classify(0, 0, set(), {})
    assert v["verdict"] == "unknown"


def test_classify_ok():
    v = mod.classify(50, 0, set(), {})
    assert v["verdict"] == "ok"


def test_classify_many_locks():
    v = mod.classify(250, 0, set(), {})
    assert v["verdict"] == "many_locks"


def test_classify_blocked_any():
    v = mod.classify(50, 1, {1234},
                          {1234: "dovecot"})
    assert v["verdict"] == "lock_blocked_any"


def test_classify_blocked_long_chain():
    v = mod.classify(50, 5, {1, 2, 3, 4, 5},
                          {1: "a", 2: "b", 3: "c",
                            4: "d", 5: "e"})
    assert v["verdict"] == "lock_blocked_long_chain"


# Priority : long_chain > blocked_any > many > ok
def test_priority_long_chain_over_any():
    v = mod.classify(50, 3, {1, 2, 3},
                          {1: "a", 2: "b", 3: "c"})
    assert v["verdict"] == "lock_blocked_long_chain"


def test_priority_any_over_many():
    v = mod.classify(250, 1, {1234},
                          {1234: "postgres"})
    assert v["verdict"] == "lock_blocked_any"


# --- status integration ----------------------------------------

def test_status_unknown(tmp_path):
    out = mod.status(None,
                       str(tmp_path / "nope_locks"),
                       str(tmp_path / "nope_proc"))
    assert out["verdict"]["verdict"] == "unknown"


def test_status_ok_synthetic(tmp_path):
    p = tmp_path / "locks"
    p.write_text(_lock_line(1) * 10)
    proc = tmp_path / "proc"
    proc.mkdir()
    out = mod.status(None, str(p), str(proc))
    assert out["total"] == 10
    assert out["blocked"] == 0
    assert out["verdict"]["verdict"] == "ok"


def test_status_blocked_synthetic(tmp_path):
    p = tmp_path / "locks"
    p.write_text(
        _lock_line(1)
        + _lock_line(2, blocked=True, pid=1234)
        + _lock_line(3, blocked=True, pid=5678))
    proc = tmp_path / "proc"
    proc.mkdir()
    _mk_proc_pid(proc, 1234, comm="dovecot")
    _mk_proc_pid(proc, 5678, comm="dovecot")
    out = mod.status(None, str(p), str(proc))
    assert out["blocked"] == 2
    assert out["verdict"]["verdict"] == "lock_blocked_any"


def test_status_long_chain_synthetic(tmp_path):
    p = tmp_path / "locks"
    p.write_text(
        "\n".join(
            _lock_line(i, blocked=True, pid=1000 + i)
            for i in range(5))
        + "\n")
    proc = tmp_path / "proc"
    proc.mkdir()
    out = mod.status(None, str(p), str(proc))
    assert out["ok"] is False
    assert (out["verdict"]["verdict"]
            == "lock_blocked_long_chain")
