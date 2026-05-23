"""Tests for modules/panic_policy.py — R&D #41.3."""
from __future__ import annotations

from pathlib import Path

import pytest

from gpu_dashboard.modules import panic_policy


def _mk_sysk(root: Path, **fields):
    root.mkdir(parents=True, exist_ok=True)
    for k, v in fields.items():
        (root / k).write_text(str(v) + "\n")


# --- read_knobs ----------------------------------------------------

def test_read_knobs_missing(tmp_path):
    assert panic_policy.read_knobs(str(tmp_path / "nope")) == {}


def test_read_knobs_basic(tmp_path):
    root = tmp_path / "k"
    _mk_sysk(root, panic=10, panic_on_oops=1, hung_task_panic=1,
             hung_task_timeout_secs=120, softlockup_panic=1,
             nmi_watchdog=1)
    k = panic_policy.read_knobs(str(root))
    assert k["panic"] == 10
    assert k["panic_on_oops"] == 1
    assert k["nmi_watchdog"] == 1


def test_read_knobs_partial(tmp_path):
    root = tmp_path / "k"
    _mk_sysk(root, panic=0)
    k = panic_policy.read_knobs(str(root))
    assert k == {"panic": 0}


def test_read_knobs_unparseable_skipped(tmp_path):
    root = tmp_path / "k"
    _mk_sysk(root, panic="notnum", panic_on_oops=1)
    k = panic_policy.read_knobs(str(root))
    assert "panic" not in k
    assert k["panic_on_oops"] == 1


# --- classify ------------------------------------------------------

def _default_knobs(**overrides):
    base = {"panic": 10, "panic_on_oops": 1,
             "hung_task_panic": 1, "hung_task_timeout_secs": 120,
             "softlockup_panic": 1, "nmi_watchdog": 1}
    base.update(overrides)
    return base


def test_classify_unknown_when_empty():
    v = panic_policy.classify({})
    assert v["verdict"] == "unknown"


def test_classify_stuck_forever_when_panic_zero():
    v = panic_policy.classify(_default_knobs(panic=0))
    assert v["verdict"] == "stuck_forever_on_panic"
    assert "panic=0" in v["reason"]


def test_classify_stuck_forever_when_panic_on_oops_zero():
    v = panic_policy.classify(_default_knobs(panic_on_oops=0))
    assert v["verdict"] == "stuck_forever_on_panic"
    assert "panic_on_oops=0" in v["reason"]


def test_classify_watchdog_disabled():
    v = panic_policy.classify(_default_knobs(nmi_watchdog=0))
    assert v["verdict"] == "watchdog_disabled"


def test_classify_silent_on_hung_task_when_headless():
    v = panic_policy.classify(_default_knobs(hung_task_panic=0),
                                host_form_factor="server")
    assert v["verdict"] == "silent_on_hung_task"
    assert "hung_task_panic=0" in v["reason"]


def test_classify_silent_on_softlockup_when_headless():
    v = panic_policy.classify(_default_knobs(softlockup_panic=0),
                                host_form_factor="vm")
    assert v["verdict"] == "silent_on_hung_task"
    assert "softlockup_panic=0" in v["reason"]


def test_classify_ok_on_desktop_without_hung_task():
    # Desktop does NOT need hung_task_panic — user is at the console.
    v = panic_policy.classify(_default_knobs(hung_task_panic=0),
                                host_form_factor="desktop")
    assert v["verdict"] == "ok_auto_reboot"


def test_classify_ok_when_all_set():
    v = panic_policy.classify(_default_knobs(),
                                host_form_factor="server")
    assert v["verdict"] == "ok_auto_reboot"


def test_classify_priority_panic_zero_beats_watchdog():
    # Both broken — panic=0 wins (worst).
    v = panic_policy.classify(_default_knobs(panic=0, nmi_watchdog=0))
    assert v["verdict"] == "stuck_forever_on_panic"


def test_classify_priority_watchdog_beats_silent():
    # Watchdog off + headless + hung_task_panic=0 — watchdog wins.
    v = panic_policy.classify(_default_knobs(nmi_watchdog=0,
                                                hung_task_panic=0),
                                host_form_factor="server")
    assert v["verdict"] == "watchdog_disabled"


# --- status integration -------------------------------------------

def test_status_with_isolated_root(monkeypatch, tmp_path):
    root = tmp_path / "k"
    _mk_sysk(root, panic=0, panic_on_oops=1, nmi_watchdog=1,
             hung_task_panic=1, softlockup_panic=1,
             hung_task_timeout_secs=120)
    monkeypatch.setattr(panic_policy, "_PROC_SYS_KERNEL", str(root))
    monkeypatch.setattr(panic_policy, "_try_host_form_factor",
                        lambda cfg: "server")
    out = panic_policy.status()
    assert out["ok"] is True
    assert out["host_form_factor"] == "server"
    assert out["verdict"]["verdict"] == "stuck_forever_on_panic"


def test_status_unknown_when_missing(monkeypatch, tmp_path):
    monkeypatch.setattr(panic_policy, "_PROC_SYS_KERNEL",
                        str(tmp_path / "nope"))
    monkeypatch.setattr(panic_policy, "_try_host_form_factor",
                        lambda cfg: None)
    out = panic_policy.status()
    assert out["ok"] is False
    assert out["verdict"]["verdict"] == "unknown"
