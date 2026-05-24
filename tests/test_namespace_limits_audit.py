"""Tests for modules/namespace_limits_audit.py — R&D #89.2."""
from __future__ import annotations

import pytest

from gpu_dashboard.modules import namespace_limits_audit as mod


def _mk_limits(tmp_path, *, overrides=None):
    d = tmp_path / "user"
    d.mkdir(parents=True, exist_ok=True)
    defaults = {ns: 123590 for ns in mod._NS_TYPES}
    if overrides:
        defaults.update(overrides)
    for ns, v in defaults.items():
        (d / f"max_{ns}_namespaces").write_text(f"{v}\n")
    return str(d)


# --- read_limits -----------------------------------------------

def test_read_limits_missing(tmp_path):
    assert mod.read_limits(str(tmp_path / "nope")) == {}


def test_read_limits_populated(tmp_path):
    r = _mk_limits(tmp_path)
    out = mod.read_limits(r)
    assert len(out) == 8
    assert out["user"] == 123590


def test_read_limits_garbage_skipped(tmp_path):
    d = tmp_path / "user"
    d.mkdir(parents=True)
    (d / "max_user_namespaces").write_text("garbage\n")
    out = mod.read_limits(str(d))
    assert "user" not in out


# --- classify --------------------------------------------------

def test_classify_unknown_empty():
    v = mod.classify({})
    assert v["verdict"] == "unknown"


def test_classify_user_ns_disabled():
    v = mod.classify({
        "user": 0, "pid": 123590, "net": 123590,
        "mnt": 123590, "ipc": 123590, "uts": 123590,
        "cgroup": 123590, "time": 123590})
    assert v["verdict"] == "user_ns_disabled"


def test_classify_ns_caps_aggressive():
    v = mod.classify({
        "user": 123590, "pid": 0, "net": 0,
        "mnt": 0, "ipc": 123590, "uts": 123590,
        "cgroup": 123590, "time": 123590})
    assert v["verdict"] == "ns_caps_aggressive"
    assert v["zeroed_types"] == ["mnt", "net", "pid"]


def test_classify_ok():
    v = mod.classify({
        "user": 123590, "pid": 123590, "net": 123590,
        "mnt": 123590, "ipc": 123590, "uts": 123590,
        "cgroup": 123590, "time": 123590})
    assert v["verdict"] == "ok"


def test_classify_two_zero_still_ok():
    # only 2 zeroed → not aggressive enough
    v = mod.classify({
        "user": 123590, "pid": 123590, "net": 0,
        "mnt": 0, "ipc": 123590, "uts": 123590,
        "cgroup": 123590, "time": 123590})
    assert v["verdict"] == "ok"


# Priority : user_ns_disabled > ns_caps_aggressive
def test_priority_user_disabled_over_aggressive():
    v = mod.classify({
        "user": 0, "pid": 0, "net": 0,
        "mnt": 0, "ipc": 123590, "uts": 123590,
        "cgroup": 123590, "time": 123590})
    assert v["verdict"] == "user_ns_disabled"


# --- status integration ----------------------------------------

def test_status_unknown(tmp_path):
    out = mod.status(None, str(tmp_path / "nope"))
    assert out["verdict"]["verdict"] == "unknown"
    assert out["ok"] is False


def test_status_ok_synthetic(tmp_path):
    r = _mk_limits(tmp_path)
    out = mod.status(None, r)
    assert out["verdict"]["verdict"] == "ok"
    assert out["ok"] is True


def test_status_user_ns_disabled_synthetic(tmp_path):
    r = _mk_limits(tmp_path, overrides={"user": 0})
    out = mod.status(None, r)
    assert out["verdict"]["verdict"] == "user_ns_disabled"
    assert out["ok"] is False


def test_status_aggressive_synthetic(tmp_path):
    r = _mk_limits(tmp_path,
                       overrides={"pid": 0, "net": 0, "mnt": 0})
    out = mod.status(None, r)
    assert out["verdict"]["verdict"] == "ns_caps_aggressive"
