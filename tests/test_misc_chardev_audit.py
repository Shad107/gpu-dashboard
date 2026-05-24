"""Tests for modules/misc_chardev_audit.py — R&D #75.2."""
from __future__ import annotations

import os

import pytest

from gpu_dashboard.modules import misc_chardev_audit as mod


# --- parse_proc_misc -------------------------------------------

def test_parse_empty():
    assert mod.parse_proc_misc("") == []
    assert mod.parse_proc_misc(None) == []


def test_parse_typical():
    text = ("232 kvm\n"
              "229 fuse\n"
              "228 hpet\n")
    out = mod.parse_proc_misc(text)
    assert len(out) == 3
    by_name = {e["name"]: e["minor"] for e in out}
    assert by_name["kvm"] == 232


# --- classify ---------------------------------------------------

def _dev_state(**overrides):
    base = {n: {"name": n, "present": False, "mode": None}
              for n in mod._WATCHED}
    base.update(overrides)
    return list(base.values())


def test_classify_unknown():
    v = mod.classify([], [], [], False, False)
    assert v["verdict"] == "unknown"


def test_classify_ok():
    state = _dev_state(
        kvm={"name": "kvm", "present": True, "mode": 0o660},
        fuse={"name": "fuse", "present": True, "mode": 0o666})
    v = mod.classify([{"name": "kvm", "minor": 232}],
                          ["kvm"],
                          state, True, True)
    assert v["verdict"] == "ok"


def test_classify_world_writable():
    state = _dev_state(
        kvm={"name": "kvm", "present": True, "mode": 0o666})
    v = mod.classify([{"name": "kvm", "minor": 232}],
                          ["kvm"],
                          state, True, True)
    assert v["verdict"] == "world_writable_node"


def test_classify_fuse_world_writable_ok():
    # fuse is on the safe-list; 0666 is fine.
    state = _dev_state(
        fuse={"name": "fuse", "present": True, "mode": 0o666})
    v = mod.classify([{"name": "fuse", "minor": 229}],
                          ["fuse"],
                          state, False, True)
    assert v["verdict"] == "ok"


def test_classify_orphan_minor():
    state = _dev_state()
    v = mod.classify(
        [{"name": "kvm", "minor": 232},
          {"name": "phantom", "minor": 999}],
        ["kvm"], state, False, True)
    assert v["verdict"] == "orphan_minor"


def test_classify_kvm_node_missing():
    # KVM module loaded, no /dev/kvm
    state = _dev_state()
    v = mod.classify(
        [{"name": "kvm", "minor": 232}],
        ["kvm"], state, True, True)
    assert v["verdict"] == "kvm_node_missing"


def test_classify_requires_root_uinput():
    state = _dev_state(
        uinput={"name": "uinput", "present": True,
                  "mode": 0o600})
    v = mod.classify(
        [{"name": "uinput", "minor": 223}],
        ["uinput"], state, False, True)
    assert v["verdict"] == "requires_root"


# Priority : ww > orphan > kvm_missing > requires_root
def test_priority_ww_over_orphan():
    state = _dev_state(
        kvm={"name": "kvm", "present": True, "mode": 0o666})
    v = mod.classify(
        [{"name": "kvm", "minor": 232},
          {"name": "phantom", "minor": 999}],
        ["kvm"], state, False, True)
    assert v["verdict"] == "world_writable_node"


def test_priority_orphan_over_kvm_missing():
    state = _dev_state()
    v = mod.classify(
        [{"name": "kvm", "minor": 232},
          {"name": "phantom", "minor": 999}],
        ["kvm"], state, True, True)
    assert v["verdict"] == "orphan_minor"


# --- status integration -----------------------------------------

def test_status_unknown(tmp_path):
    out = mod.status(None,
                          str(tmp_path / "no_misc"),
                          str(tmp_path / "no_sys"),
                          str(tmp_path / "no_kvm"),
                          str(tmp_path / "no_dev"))
    assert out["ok"] is False
    assert out["verdict"]["verdict"] == "unknown"


def test_status_ok_synthetic(tmp_path):
    pm = tmp_path / "misc"
    pm.write_text("232 kvm\n229 fuse\n")
    sm = tmp_path / "sysmisc"; sm.mkdir()
    (sm / "kvm").mkdir()
    (sm / "fuse").mkdir()
    dev = tmp_path / "dev"; dev.mkdir()
    (dev / "kvm").write_text("")
    os.chmod(str(dev / "kvm"), 0o660)
    (dev / "fuse").write_text("")
    os.chmod(str(dev / "fuse"), 0o666)
    out = mod.status(None, str(pm), str(sm),
                          str(tmp_path / "no_kvm_mod"),
                          str(dev))
    assert out["ok"] is True
    assert out["verdict"]["verdict"] == "ok"


def test_status_world_writable(tmp_path):
    pm = tmp_path / "misc"
    pm.write_text("232 kvm\n")
    sm = tmp_path / "sysmisc"; sm.mkdir()
    (sm / "kvm").mkdir()
    dev = tmp_path / "dev"; dev.mkdir()
    (dev / "kvm").write_text("")
    os.chmod(str(dev / "kvm"), 0o666)
    out = mod.status(None, str(pm), str(sm),
                          str(tmp_path / "no_kvm_mod"),
                          str(dev))
    assert out["verdict"]["verdict"] == "world_writable_node"
