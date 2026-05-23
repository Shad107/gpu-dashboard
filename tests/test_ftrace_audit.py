"""Tests for modules/ftrace_audit.py — R&D #48.1."""
from __future__ import annotations

import pytest

from gpu_dashboard.modules import ftrace_audit as mod


def _mk_state(root, **fields):
    root.mkdir(parents=True, exist_ok=True)
    if "current_tracer" in fields:
        (root / "current_tracer").write_text(fields["current_tracer"] + "\n")
    if "tracing_on" in fields:
        (root / "tracing_on").write_text(str(fields["tracing_on"]) + "\n")
    if "kprobe_events" in fields:
        (root / "kprobe_events").write_text(fields["kprobe_events"])
    if "uprobe_events" in fields:
        (root / "uprobe_events").write_text(fields["uprobe_events"])
    if "set_event" in fields:
        (root / "set_event").write_text(fields["set_event"])


# --- read_state ---------------------------------------------------

def test_read_state_basic(tmp_path):
    _mk_state(tmp_path / "tr", current_tracer="nop", tracing_on=0,
                kprobe_events="", uprobe_events="", set_event="")
    s = mod.read_state(str(tmp_path / "tr"))
    assert s["available"] is True
    assert s["current_tracer"] == "nop"
    assert s["tracing_on"] == 0


def test_read_state_missing(tmp_path):
    s = mod.read_state(str(tmp_path / "nope"))
    assert s == {"available": False}


def test_read_state_with_kprobes(tmp_path):
    _mk_state(tmp_path / "tr", current_tracer="nop",
                kprobe_events="p:my_probe do_sys_open\n")
    s = mod.read_state(str(tmp_path / "tr"))
    assert len(s["kprobe_events"]) == 1


# --- classify -----------------------------------------------------

def _state(**o):
    base = {"available": True, "current_tracer": "nop",
              "tracing_on": 0, "kprobe_events": [],
              "uprobe_events": [], "set_event_count": 0}
    base.update(o)
    return base


def test_classify_requires_root():
    v = mod.classify({"available": True}, requires_root=True)
    assert v["verdict"] == "requires_root"


def test_classify_unknown_no_dir():
    v = mod.classify({"available": False}, requires_root=False)
    assert v["verdict"] == "unknown"


def test_classify_ok():
    v = mod.classify(_state(), requires_root=False)
    assert v["verdict"] == "ok"


def test_classify_tracer_left_on():
    v = mod.classify(_state(current_tracer="function",
                              tracing_on=1),
                       requires_root=False)
    assert v["verdict"] == "tracer_left_on"


def test_classify_orphan_kprobes():
    v = mod.classify(_state(
        kprobe_events=["p:my_probe do_sys_open"]),
        requires_root=False)
    assert v["verdict"] == "orphan_kprobes"


def test_classify_orphan_uprobes():
    v = mod.classify(_state(
        uprobe_events=["p:my_uprobe /bin/ls:0x100"]),
        requires_root=False)
    assert v["verdict"] == "orphan_uprobes"


def test_classify_events_enabled():
    v = mod.classify(_state(set_event_count=5),
                       requires_root=False)
    assert v["verdict"] == "events_enabled"


def test_classify_priority_tracer_wins():
    v = mod.classify(_state(current_tracer="function",
                              tracing_on=1,
                              kprobe_events=["p:x"],
                              set_event_count=3),
                       requires_root=False)
    assert v["verdict"] == "tracer_left_on"


def test_classify_priority_kprobes_over_uprobes():
    v = mod.classify(_state(kprobe_events=["p:a"],
                              uprobe_events=["p:b"]),
                       requires_root=False)
    assert v["verdict"] == "orphan_kprobes"


# --- status integration ------------------------------------------

def test_status_no_tracing(monkeypatch, tmp_path):
    monkeypatch.setattr(mod, "_SYS_TRACING", str(tmp_path / "nope"))
    out = mod.status()
    assert out["ok"] is False
    assert out["verdict"]["verdict"] == "unknown"


def test_status_ok(monkeypatch, tmp_path):
    _mk_state(tmp_path / "tr", current_tracer="nop", tracing_on=0,
                kprobe_events="", uprobe_events="", set_event="")
    monkeypatch.setattr(mod, "_SYS_TRACING", str(tmp_path / "tr"))
    out = mod.status()
    assert out["ok"] is True
    assert out["verdict"]["verdict"] == "ok"


def test_status_tracer_left_on(monkeypatch, tmp_path):
    _mk_state(tmp_path / "tr", current_tracer="function",
                tracing_on=1, kprobe_events="",
                uprobe_events="", set_event="")
    monkeypatch.setattr(mod, "_SYS_TRACING", str(tmp_path / "tr"))
    out = mod.status()
    assert out["verdict"]["verdict"] == "tracer_left_on"
