"""Tests for modules/tcp_congestion_control_audit.py
R&D #89.1."""
from __future__ import annotations

import pytest

from gpu_dashboard.modules import (
    tcp_congestion_control_audit as mod)


def _mk_proc_net(tmp_path, *, cc="cubic",
                   available="reno cubic bbr",
                   tfo="1"):
    d = tmp_path / "net"
    d.mkdir(parents=True, exist_ok=True)
    (d / "tcp_congestion_control").write_text(cc + "\n")
    (d / "tcp_available_congestion_control").write_text(
        available + "\n")
    (d / "tcp_fastopen").write_text(tfo + "\n")
    return str(d)


# --- read_tcp_state --------------------------------------------

def test_read_tcp_state_missing(tmp_path):
    out = mod.read_tcp_state(str(tmp_path / "nope"))
    assert out["current_cc"] == ""
    assert out["available_cc"] == []
    assert out["tcp_fastopen"] is None


def test_read_tcp_state_populated(tmp_path):
    r = _mk_proc_net(tmp_path)
    out = mod.read_tcp_state(r)
    assert out["current_cc"] == "cubic"
    assert "bbr" in out["available_cc"]
    assert out["tcp_fastopen"] == 1


def test_read_tcp_state_garbage_tfo(tmp_path):
    r = _mk_proc_net(tmp_path, tfo="garbage")
    out = mod.read_tcp_state(r)
    assert out["tcp_fastopen"] is None


# --- classify --------------------------------------------------

def test_classify_unknown_no_cc():
    v = mod.classify({"current_cc": "", "available_cc": [],
                          "tcp_fastopen": None})
    assert v["verdict"] == "unknown"


def test_classify_bbr_available_unused():
    v = mod.classify({
        "current_cc": "cubic",
        "available_cc": ["reno", "cubic", "bbr"],
        "tcp_fastopen": 1})
    assert v["verdict"] == "bbr_available_unused"


def test_classify_tfo_off():
    v = mod.classify({
        "current_cc": "bbr",
        "available_cc": ["reno", "cubic", "bbr"],
        "tcp_fastopen": 0})
    assert v["verdict"] == "tfo_off"


def test_classify_ok_bbr_active():
    v = mod.classify({
        "current_cc": "bbr",
        "available_cc": ["reno", "cubic", "bbr"],
        "tcp_fastopen": 1})
    assert v["verdict"] == "ok"


def test_classify_ok_bbr_unavailable():
    # Kernel built without bbr — cubic is appropriate
    v = mod.classify({
        "current_cc": "cubic",
        "available_cc": ["reno", "cubic"],
        "tcp_fastopen": 1})
    assert v["verdict"] == "ok"


# Priority : bbr_unused > tfo_off > ok
def test_priority_bbr_over_tfo():
    v = mod.classify({
        "current_cc": "cubic",
        "available_cc": ["reno", "cubic", "bbr"],
        "tcp_fastopen": 0})
    assert v["verdict"] == "bbr_available_unused"


# --- status integration ----------------------------------------

def test_status_unknown(tmp_path):
    out = mod.status(None, str(tmp_path / "nope"))
    assert out["verdict"]["verdict"] == "unknown"
    assert out["ok"] is False


def test_status_bbr_available_synthetic(tmp_path):
    r = _mk_proc_net(tmp_path)
    out = mod.status(None, r)
    assert out["verdict"]["verdict"] == "bbr_available_unused"
    assert out["current_cc"] == "cubic"
    assert out["ok"] is False


def test_status_ok_synthetic(tmp_path):
    r = _mk_proc_net(tmp_path, cc="bbr")
    out = mod.status(None, r)
    assert out["verdict"]["verdict"] == "ok"
    assert out["ok"] is True


def test_status_tfo_off_synthetic(tmp_path):
    r = _mk_proc_net(tmp_path, cc="cubic",
                       available="reno cubic", tfo="0")
    out = mod.status(None, r)
    assert out["verdict"]["verdict"] == "tfo_off"
