"""Tests for modules/lsm_subtree_audit.py — R&D #75.1."""
from __future__ import annotations

import os

import pytest

from gpu_dashboard.modules import lsm_subtree_audit as mod


def _mk_security(root, *, lsm_stack=None, lockdown=None,
                       subdirs=None, apparmor_profiles=None):
    root.mkdir(parents=True, exist_ok=True)
    if lsm_stack is not None:
        (root / "lsm").write_text(",".join(lsm_stack) + "\n")
    if lockdown is not None:
        (root / "lockdown").write_text(lockdown + "\n")
    for n in subdirs or []:
        (root / n).mkdir(exist_ok=True)
    if apparmor_profiles is not None:
        ap = root / "apparmor"
        ap.mkdir(exist_ok=True)
        (ap / "profiles").write_text(
            "\n".join(f"profile{i} (enforce)"
                          for i in range(apparmor_profiles))
            + ("\n" if apparmor_profiles else ""))


# --- list_lsm_stack --------------------------------------------

def test_list_stack_missing(tmp_path):
    assert mod.list_lsm_stack(str(tmp_path / "nope")) == []


def test_list_stack_full(tmp_path):
    _mk_security(tmp_path,
                       lsm_stack=["lockdown", "capability",
                                       "landlock", "yama",
                                       "apparmor"])
    out = mod.list_lsm_stack(str(tmp_path))
    assert out == ["lockdown", "capability", "landlock",
                       "yama", "apparmor"]


# --- read_lockdown ---------------------------------------------

def test_read_lockdown_missing(tmp_path):
    assert mod.read_lockdown(str(tmp_path)) is None


def test_read_lockdown_none(tmp_path):
    _mk_security(tmp_path,
                       lockdown="[none] integrity confidentiality")
    assert mod.read_lockdown(str(tmp_path)) == "none"


def test_read_lockdown_integrity(tmp_path):
    _mk_security(tmp_path,
                       lockdown="none [integrity] confidentiality")
    assert mod.read_lockdown(str(tmp_path)) == "integrity"


# --- apparmor_profile_count ------------------------------------

def test_apparmor_count_missing(tmp_path):
    _mk_security(tmp_path)
    assert mod.apparmor_profile_count(str(tmp_path)) is None


def test_apparmor_count_zero(tmp_path):
    _mk_security(tmp_path, apparmor_profiles=0)
    assert mod.apparmor_profile_count(str(tmp_path)) == 0


def test_apparmor_count_three(tmp_path):
    _mk_security(tmp_path, apparmor_profiles=3)
    assert mod.apparmor_profile_count(str(tmp_path)) == 3


# --- classify ---------------------------------------------------

def test_classify_unknown():
    v = mod.classify(False, [], [], None, None, False, False)
    assert v["verdict"] == "unknown"


def test_classify_lsm_disabled_only_capability():
    v = mod.classify(True, ["capability"], [], None, None,
                          True, False)
    assert v["verdict"] == "lsm_disabled"


def test_classify_lsm_disabled_empty():
    v = mod.classify(True, [], [], None, None, True, False)
    assert v["verdict"] == "lsm_disabled"


def test_classify_policy_unloaded():
    v = mod.classify(True,
                          ["capability", "apparmor"],
                          ["apparmor"], "none", 0,
                          True, True)
    assert v["verdict"] == "policy_unloaded"


def test_classify_requires_root():
    v = mod.classify(True,
                          ["capability", "apparmor", "ima"],
                          ["apparmor", "ima"],
                          "none", None,
                          False, True)
    assert v["verdict"] == "requires_root"


def test_classify_stacked_partial():
    # apparmor listed in lsm but no apparmor/ subdir
    v = mod.classify(True,
                          ["capability", "apparmor"],
                          ["lockdown"], "none", None,
                          True, False)
    assert v["verdict"] == "stacked_partial"


def test_classify_ok():
    v = mod.classify(True,
                          ["capability", "landlock", "apparmor"],
                          ["apparmor", "lockdown"],
                          "none", 50,
                          True, True)
    assert v["verdict"] == "ok"


# Priority : lsm_disabled > policy_unloaded > requires_root >
# stacked_partial
def test_priority_lsm_disabled_over_policy_unloaded():
    v = mod.classify(True, ["capability"], [], None, 0,
                          True, True)
    assert v["verdict"] == "lsm_disabled"


def test_priority_policy_unloaded_over_requires_root():
    v = mod.classify(True,
                          ["apparmor"], ["apparmor"], "none", 0,
                          False, True)
    assert v["verdict"] == "policy_unloaded"


# --- status integration -----------------------------------------

def test_status_unknown(tmp_path):
    out = mod.status(None, str(tmp_path / "nope"))
    assert out["ok"] is False
    assert out["verdict"]["verdict"] == "unknown"


def test_status_ok_synthetic(tmp_path):
    # Note : real kernel exposes 'lockdown' as a FILE not a
    # directory. Don't add it to subdirs.
    _mk_security(tmp_path,
                       lsm_stack=["capability", "landlock",
                                       "apparmor"],
                       lockdown="[none] integrity",
                       subdirs=["apparmor"],
                       apparmor_profiles=50)
    out = mod.status(None, str(tmp_path))
    assert out["ok"] is True
    assert out["apparmor_profile_count"] == 50
    assert out["lockdown"] == "none"
    assert out["verdict"]["verdict"] == "ok"


def test_status_stacked_partial_synthetic(tmp_path):
    # IMA listed but no /sys/kernel/security/ima/ subdir
    _mk_security(tmp_path,
                       lsm_stack=["capability", "ima"],
                       lockdown="[none] integrity",
                       subdirs=[])
    out = mod.status(None, str(tmp_path))
    assert out["verdict"]["verdict"] == "stacked_partial"
