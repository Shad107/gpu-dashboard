"""Tests for modules/security_posture.py — R&D #46.2."""
from __future__ import annotations

import pytest

from gpu_dashboard.modules import security_posture as mod


# --- parse_lockdown -----------------------------------------------

def test_parse_lockdown_none_active():
    a, av = mod.parse_lockdown("[none] integrity confidentiality")
    assert a == "none"
    assert "integrity" in av
    assert "confidentiality" in av


def test_parse_lockdown_integrity_active():
    a, av = mod.parse_lockdown("none [integrity] confidentiality")
    assert a == "integrity"


def test_parse_lockdown_empty():
    assert mod.parse_lockdown("") == (None, [])
    assert mod.parse_lockdown(None) == (None, [])


# --- read_sysctls / read_security ---------------------------------

def test_read_sysctls(tmp_path):
    root = tmp_path / "k"
    (root / "yama").mkdir(parents=True)
    (root / "perf_event_paranoid").write_text("3\n")
    (root / "yama" / "ptrace_scope").write_text("1\n")
    (root / "kptr_restrict").write_text("2\n")
    out = mod.read_sysctls(str(root))
    assert out["perf_event_paranoid"] == 3
    assert out["ptrace_scope"] == 1
    assert out["kptr_restrict"] == 2


def test_read_security(tmp_path):
    root = tmp_path / "sec"
    root.mkdir()
    (root / "lsm").write_text("capability,landlock,yama,apparmor\n")
    (root / "lockdown").write_text("[none] integrity confidentiality\n")
    out = mod.read_security(str(root))
    assert "yama" in out["lsm"]
    assert out["lockdown"] == "none"


# --- classify ------------------------------------------------------

def _sysctls(**o):
    base = {"perf_event_paranoid": 4, "ptrace_scope": 1,
              "kptr_restrict": 2, "dmesg_restrict": 1}
    base.update(o)
    return base


def _security(lockdown="none", lsm=None):
    return {"lockdown": lockdown,
              "lockdown_available": ["none", "integrity", "confidentiality"],
              "lsm": lsm or ["capability", "yama", "apparmor"]}


def test_classify_unknown_when_empty():
    v = mod.classify({}, {})
    assert v["verdict"] == "unknown"


def test_classify_ok():
    v = mod.classify(_sysctls(), _security())
    assert v["verdict"] == "ok"


def test_classify_paranoid_too_loose_ptrace():
    v = mod.classify(_sysctls(ptrace_scope=0), _security())
    assert v["verdict"] == "paranoid_too_loose"
    assert "ptrace_scope=0" in v["reason"]


def test_classify_paranoid_too_loose_kptr():
    v = mod.classify(_sysctls(kptr_restrict=0), _security())
    assert v["verdict"] == "paranoid_too_loose"


def test_classify_paranoid_too_loose_perf_event():
    v = mod.classify(_sysctls(perf_event_paranoid=1), _security())
    assert v["verdict"] == "paranoid_too_loose"


def test_classify_lockdown_confined():
    v = mod.classify(_sysctls(), _security(lockdown="integrity"))
    assert v["verdict"] == "lockdown_confined"
    assert "integrity" in v["reason"]


def test_classify_loose_wins_over_lockdown():
    v = mod.classify(_sysctls(ptrace_scope=0),
                       _security(lockdown="integrity"))
    assert v["verdict"] == "paranoid_too_loose"


# --- status integration -------------------------------------------

def test_status_with_isolated(monkeypatch, tmp_path):
    k = tmp_path / "k"
    (k / "yama").mkdir(parents=True)
    (k / "perf_event_paranoid").write_text("4\n")
    (k / "yama" / "ptrace_scope").write_text("1\n")
    (k / "kptr_restrict").write_text("2\n")
    (k / "dmesg_restrict").write_text("1\n")
    sec = tmp_path / "sec"
    sec.mkdir()
    (sec / "lsm").write_text("capability,yama,apparmor\n")
    (sec / "lockdown").write_text("[none] integrity\n")
    monkeypatch.setattr(mod, "_PROC_SYS_KERNEL", str(k))
    monkeypatch.setattr(mod, "_SYS_SECURITY", str(sec))
    out = mod.status()
    assert out["ok"] is True
    assert out["verdict"]["verdict"] == "ok"


def test_status_unknown(monkeypatch, tmp_path):
    monkeypatch.setattr(mod, "_PROC_SYS_KERNEL",
                        str(tmp_path / "nope"))
    monkeypatch.setattr(mod, "_SYS_SECURITY",
                        str(tmp_path / "nope2"))
    out = mod.status()
    assert out["ok"] is False
    assert out["verdict"]["verdict"] == "unknown"
