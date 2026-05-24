"""Tests for modules/suspend_stats_audit.py — R&D #84.1."""
from __future__ import annotations

import pytest

from gpu_dashboard.modules import suspend_stats_audit as mod


def _mk_stats(tmp_path, *, success=0, fail=0,
                last_dev="", last_errno=0, last_step="",
                **step_fails):
    d = tmp_path / "suspend_stats"
    d.mkdir(parents=True, exist_ok=True)
    (d / "success").write_text(f"{success}\n")
    (d / "fail").write_text(f"{fail}\n")
    (d / "last_failed_dev").write_text(last_dev + "\n")
    (d / "last_failed_errno").write_text(f"{last_errno}\n")
    (d / "last_failed_step").write_text(last_step + "\n")
    for s in mod._FAILED_STEPS:
        (d / s).write_text(f"{step_fails.get(s, 0)}\n")
    return str(d)


# --- read_stats ------------------------------------------------

def test_read_stats_missing(tmp_path):
    out = mod.read_stats(str(tmp_path / "nope"))
    assert out["present"] is False


def test_read_stats_populated(tmp_path):
    r = _mk_stats(tmp_path, success=42, fail=2,
                    last_dev="nvidia", last_errno=-16,
                    last_step="resume",
                    failed_resume=2)
    out = mod.read_stats(r)
    assert out["present"] is True
    assert out["success"] == 42
    assert out["fail"] == 2
    assert out["last_failed_dev"] == "nvidia"
    assert out["last_failed_errno"] == -16
    assert out["failed_resume"] == 2


# --- classify --------------------------------------------------

def test_classify_unknown():
    v = mod.classify({"present": False})
    assert v["verdict"] == "unknown"


def test_classify_never_exercised():
    s = {"present": True, "success": 0, "fail": 0,
          "last_failed_dev": "", "last_failed_errno": 0,
          "last_failed_step": ""}
    for step in mod._FAILED_STEPS:
        s[step] = 0
    v = mod.classify(s)
    assert v["verdict"] == "suspend_never_exercised"


def test_classify_ok():
    s = {"present": True, "success": 100, "fail": 0,
          "last_failed_dev": "", "last_failed_errno": 0,
          "last_failed_step": ""}
    for step in mod._FAILED_STEPS:
        s[step] = 0
    v = mod.classify(s)
    assert v["verdict"] == "ok"


def test_classify_suspend_had_failures():
    s = {"present": True, "success": 50, "fail": 2,
          "last_failed_dev": "nvidia",
          "last_failed_errno": -16,
          "last_failed_step": "resume"}
    for step in mod._FAILED_STEPS:
        s[step] = 0
    s["failed_resume"] = 2
    v = mod.classify(s)
    assert v["verdict"] == "suspend_had_failures"


def test_classify_suspend_failing():
    s = {"present": True, "success": 10, "fail": 8,
          "last_failed_dev": "rtl8821ce",
          "last_failed_errno": -16,
          "last_failed_step": "noirq"}
    for step in mod._FAILED_STEPS:
        s[step] = 0
    s["failed_suspend_noirq"] = 5
    v = mod.classify(s)
    assert v["verdict"] == "suspend_failing"


def test_classify_failing_below_floor_falls_to_warn():
    s = {"present": True, "success": 10, "fail": 3,
          "last_failed_dev": "x", "last_failed_errno": -16,
          "last_failed_step": "resume"}
    for step in mod._FAILED_STEPS:
        s[step] = 0
    s["failed_resume"] = 3
    v = mod.classify(s)
    assert v["verdict"] == "suspend_had_failures"


def test_classify_step_counter_only_triggers_warn():
    # No fail counter but step counter is non-zero
    s = {"present": True, "success": 10, "fail": 0,
          "last_failed_dev": "", "last_failed_errno": 0,
          "last_failed_step": ""}
    for step in mod._FAILED_STEPS:
        s[step] = 0
    s["failed_freeze"] = 1
    v = mod.classify(s)
    assert v["verdict"] == "suspend_had_failures"


# Priority : failing > had_failures > never > ok
def test_priority_failing_over_had_failures():
    s = {"present": True, "success": 1, "fail": 10,
          "last_failed_dev": "x", "last_failed_errno": -5,
          "last_failed_step": "resume"}
    for step in mod._FAILED_STEPS:
        s[step] = 0
    v = mod.classify(s)
    assert v["verdict"] == "suspend_failing"


# --- status integration ----------------------------------------

def test_status_unknown(tmp_path):
    out = mod.status(None, str(tmp_path / "nope"))
    assert out["ok"] is False
    assert out["verdict"]["verdict"] == "unknown"


def test_status_never_exercised(tmp_path):
    r = _mk_stats(tmp_path, success=0, fail=0)
    out = mod.status(None, r)
    assert out["ok"] is True   # accent is acceptable
    assert (out["verdict"]["verdict"]
            == "suspend_never_exercised")


def test_status_ok(tmp_path):
    r = _mk_stats(tmp_path, success=50, fail=0)
    out = mod.status(None, r)
    assert out["ok"] is True
    assert out["verdict"]["verdict"] == "ok"


def test_status_suspend_failing(tmp_path):
    r = _mk_stats(tmp_path, success=2, fail=10,
                    last_dev="nvidia", last_errno=-16,
                    last_step="resume", failed_resume=10)
    out = mod.status(None, r)
    assert out["ok"] is False
    assert out["verdict"]["verdict"] == "suspend_failing"
