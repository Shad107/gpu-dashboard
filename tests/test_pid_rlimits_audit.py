"""Tests for modules/pid_rlimits_audit.py — R&D #59.4."""
from __future__ import annotations

import pytest

from gpu_dashboard.modules import pid_rlimits_audit as mod


LIMITS_GOOD = """\
Limit                     Soft Limit           Hard Limit           Units
Max cpu time              unlimited            unlimited            seconds
Max file size             unlimited            unlimited            bytes
Max data size             unlimited            unlimited            bytes
Max stack size            8388608              unlimited            bytes
Max core file size        0                    unlimited            bytes
Max resident set          unlimited            unlimited            bytes
Max processes             123590               123590               processes
Max open files            65536                65536                files
Max locked memory         unlimited            unlimited            bytes
Max address space         unlimited            unlimited            bytes
Max file locks            unlimited            unlimited            locks
Max pending signals       123590               123590               signals
Max msgqueue size         819200               819200               bytes
Max nice priority         0                    0
Max realtime priority     0                    0
Max realtime timeout      unlimited            unlimited            us
"""

LIMITS_BAD_MEMLOCK = """\
Limit                     Soft Limit           Hard Limit           Units
Max open files            65536                65536                files
Max locked memory         65536                65536                bytes
Max address space         unlimited            unlimited            bytes
Max processes             123590               123590               processes
"""

LIMITS_BAD_NOFILE = """\
Limit                     Soft Limit           Hard Limit           Units
Max open files            1024                 524288               files
Max locked memory         unlimited            unlimited            bytes
Max address space         unlimited            unlimited            bytes
Max processes             123590               123590               processes
"""


# --- parse_limits -----------------------------------------------

def test_parse_limits_good():
    out = mod.parse_limits(LIMITS_GOOD)
    assert out["Max open files"] == ("65536", "65536")
    assert out["Max locked memory"] == ("unlimited", "unlimited")
    assert out["Max processes"] == ("123590", "123590")


def test_parse_limits_empty():
    assert mod.parse_limits("") == {}
    assert mod.parse_limits(None) == {}


def test_parse_limits_no_units():
    # Some lines (Max nice priority) have no units token
    out = mod.parse_limits(LIMITS_GOOD)
    assert out["Max nice priority"] == ("0", "0")


# --- find_llm_processes -----------------------------------------

def test_find_llm_processes(tmp_path):
    # synthesize /proc layout
    for pid, comm in [
        ("123", "llama-server\n"),
        ("456", "bash\n"),
        ("789", "vllm-worker\n"),
        ("notapid", "ignored\n"),
    ]:
        d = tmp_path / pid
        d.mkdir(parents=True, exist_ok=True)
        (d / "comm").write_text(comm)
    out = mod.find_llm_processes(str(tmp_path))
    pids = [p["pid"] for p in out]
    assert 123 in pids
    assert 789 in pids
    assert 456 not in pids


# --- classify ---------------------------------------------------

def _candidate(comm="llama-server", pid=123,
                 limits_text=LIMITS_GOOD):
    return {"pid": pid, "comm": comm,
              "limits": mod.parse_limits(limits_text)}


def test_classify_unknown():
    v = mod.classify([])
    assert v["verdict"] == "unknown"


def test_classify_ok():
    v = mod.classify([_candidate()])
    assert v["verdict"] == "ok"


def test_classify_memlock_low():
    v = mod.classify([_candidate(limits_text=LIMITS_BAD_MEMLOCK)])
    assert v["verdict"] == "memlock_too_low_for_mmap_lock"


def test_classify_nofile_low():
    v = mod.classify([_candidate(limits_text=LIMITS_BAD_NOFILE)])
    assert v["verdict"] == "nofile_lt_4096"


def test_classify_priority_memlock_wins():
    # memlock low + nofile low → memlock priority.
    bad = (LIMITS_BAD_MEMLOCK
              .replace("Max open files            65536",
                          "Max open files            1024 "))
    v = mod.classify([_candidate(limits_text=bad)])
    assert v["verdict"] == "memlock_too_low_for_mmap_lock"


# --- status integration -----------------------------------------

def test_status_unknown(tmp_path):
    out = mod.status(None, str(tmp_path / "noproc"))
    assert out["ok"] is False
    assert out["verdict"]["verdict"] == "unknown"


def test_status_with_self_only(tmp_path):
    # synth /proc/self/limits = good
    s = tmp_path / "self"
    s.mkdir()
    (s / "limits").write_text(LIMITS_GOOD)
    out = mod.status(None, str(tmp_path))
    assert out["ok"] is True
    assert out["candidate_count"] == 1
    assert out["verdict"]["verdict"] == "ok"
