"""Tests for modules/kernel_lockup_watchdog_audit.py
R&D #92.2."""
from __future__ import annotations

import pytest

from gpu_dashboard.modules import (
    kernel_lockup_watchdog_audit as mod)


def _mk_proc_sys_kernel(tmp_path, *, watchdog="1",
                          nmi_watchdog="1",
                          watchdog_thresh="10",
                          soft_watchdog="1"):
    d = tmp_path / "sys_kernel"
    d.mkdir(parents=True, exist_ok=True)
    if watchdog is not None:
        (d / "watchdog").write_text(watchdog + "\n")
    if nmi_watchdog is not None:
        (d / "nmi_watchdog").write_text(nmi_watchdog + "\n")
    if watchdog_thresh is not None:
        (d / "watchdog_thresh").write_text(
            watchdog_thresh + "\n")
    if soft_watchdog is not None:
        (d / "soft_watchdog").write_text(soft_watchdog + "\n")
    return str(d)


def _mk_sys_cpu(tmp_path, *, nmi_hw_supported=True):
    d = tmp_path / "sys_cpu"
    d.mkdir(parents=True, exist_ok=True)
    if nmi_hw_supported:
        (d / "nmi_watchdog").write_text("1\n")
    return str(d)


# --- read_watchdog_state ---------------------------------------

def test_read_state_missing(tmp_path):
    out = mod.read_watchdog_state(
        str(tmp_path / "nope"), str(tmp_path / "no_cpu"))
    assert out["watchdog"] is None


def test_read_state_full(tmp_path):
    p = _mk_proc_sys_kernel(tmp_path)
    c = _mk_sys_cpu(tmp_path)
    out = mod.read_watchdog_state(p, c)
    assert out["watchdog"] == 1
    assert out["nmi_hw_supported"] is True


# --- classify --------------------------------------------------

def _state(**kwargs):
    base = {"watchdog": 1, "nmi_watchdog": 1,
            "watchdog_thresh": 10, "soft_watchdog": 1,
            "nmi_hw_supported": True}
    base.update(kwargs)
    return base


def test_classify_unknown():
    v = mod.classify(_state(watchdog=None, nmi_watchdog=None))
    assert v["verdict"] == "unknown"


def test_classify_fully_disabled():
    v = mod.classify(_state(watchdog=0, nmi_watchdog=0))
    assert v["verdict"] == "watchdog_fully_disabled"


def test_classify_nmi_disabled_hw_supported():
    v = mod.classify(_state(watchdog=1, nmi_watchdog=0,
                                 nmi_hw_supported=True))
    assert v["verdict"] == "nmi_watchdog_disabled"


def test_classify_nmi_off_no_hw_is_ok():
    # No HW support → not a fault
    v = mod.classify(_state(watchdog=1, nmi_watchdog=0,
                                 nmi_hw_supported=False))
    assert v["verdict"] == "ok"


def test_classify_thresh_high_accent():
    v = mod.classify(_state(watchdog_thresh=60))
    assert v["verdict"] == "watchdog_thresh_high"


def test_classify_thresh_at_boundary_is_ok():
    # 30 is the threshold, not > 30 — so 30 is still ok
    v = mod.classify(_state(watchdog_thresh=30))
    assert v["verdict"] == "ok"


def test_classify_ok():
    v = mod.classify(_state())
    assert v["verdict"] == "ok"


# Priority : fully_disabled > nmi_disabled > thresh_high
def test_priority_fully_disabled_over_nmi():
    v = mod.classify(_state(watchdog=0, nmi_watchdog=0,
                                 watchdog_thresh=60))
    assert v["verdict"] == "watchdog_fully_disabled"


def test_priority_nmi_over_thresh():
    v = mod.classify(_state(watchdog=1, nmi_watchdog=0,
                                 nmi_hw_supported=True,
                                 watchdog_thresh=60))
    assert v["verdict"] == "nmi_watchdog_disabled"


# --- status integration ----------------------------------------

def test_status_unknown(tmp_path):
    out = mod.status(None,
                     str(tmp_path / "nope"),
                     str(tmp_path / "no_cpu"))
    assert out["verdict"]["verdict"] == "unknown"


def test_status_ok_synthetic(tmp_path):
    p = _mk_proc_sys_kernel(tmp_path)
    c = _mk_sys_cpu(tmp_path)
    out = mod.status(None, p, c)
    assert out["verdict"]["verdict"] == "ok"


def test_status_nmi_disabled_synthetic(tmp_path):
    p = _mk_proc_sys_kernel(tmp_path, nmi_watchdog="0")
    c = _mk_sys_cpu(tmp_path)
    out = mod.status(None, p, c)
    assert (out["verdict"]["verdict"]
            == "nmi_watchdog_disabled")
    assert out["ok"] is False


def test_status_fully_disabled_synthetic(tmp_path):
    p = _mk_proc_sys_kernel(tmp_path,
                                  watchdog="0", nmi_watchdog="0")
    c = _mk_sys_cpu(tmp_path)
    out = mod.status(None, p, c)
    assert out["verdict"]["verdict"] == "watchdog_fully_disabled"
