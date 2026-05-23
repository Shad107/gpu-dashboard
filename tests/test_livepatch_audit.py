"""Tests for modules/livepatch_audit.py — R&D #57.1."""
from __future__ import annotations

import pytest

from gpu_dashboard.modules import livepatch_audit as mod


def _mk_patch(root, name, *, enabled=1, transition=0,
                signed=True):
    d = root / name
    d.mkdir(parents=True, exist_ok=True)
    (d / "enabled").write_text(f"{enabled}\n")
    (d / "transition").write_text(f"{transition}\n")
    if signed:
        (d / "signature").write_text("...\n")
    return d


# --- list_patches -----------------------------------------------

def test_list_patches_missing(tmp_path):
    assert mod.list_patches(str(tmp_path / "nope")) == []


def test_list_patches_empty(tmp_path):
    assert mod.list_patches(str(tmp_path)) == []


def test_list_patches(tmp_path):
    _mk_patch(tmp_path, "kpatch_cve_2023_4622", enabled=1,
                signed=True)
    _mk_patch(tmp_path, "test_unsigned", enabled=1, signed=False)
    out = mod.list_patches(str(tmp_path))
    assert len(out) == 2
    kp = next(p for p in out if p["name"].startswith("kpatch"))
    assert kp["enabled"] == 1
    assert kp["has_signature"] is True
    us = next(p for p in out if p["name"] == "test_unsigned")
    assert us["has_signature"] is False


# --- classify ---------------------------------------------------

def _patch(name="kpatch1", enabled=1, transition=0,
            has_signature=True):
    return {"name": name, "enabled": enabled,
              "transition": transition,
              "has_signature": has_signature}


def test_classify_unknown():
    v = mod.classify([], livepatch_present=False,
                       replace_val=None)
    assert v["verdict"] == "unknown"


def test_classify_ok_no_patches():
    v = mod.classify([], livepatch_present=True,
                       replace_val=1)
    assert v["verdict"] == "ok"


def test_classify_ok_enabled_signed():
    v = mod.classify([_patch()],
                       livepatch_present=True, replace_val=1)
    assert v["verdict"] == "ok"


def test_classify_stuck_transition():
    v = mod.classify([_patch(transition=1)],
                       livepatch_present=True, replace_val=1)
    assert v["verdict"] == "stuck_transition"


def test_classify_unsigned():
    v = mod.classify([_patch(has_signature=False)],
                       livepatch_present=True, replace_val=1)
    assert v["verdict"] == "unsigned_patch"


def test_classify_disabled():
    v = mod.classify([_patch(enabled=0)],
                       livepatch_present=True, replace_val=1)
    assert v["verdict"] == "disabled_patch"


def test_classify_priority_stuck_wins():
    v = mod.classify(
        [_patch(transition=1, has_signature=False, enabled=0)],
        livepatch_present=True, replace_val=1)
    assert v["verdict"] == "stuck_transition"


# --- status integration -----------------------------------------

def test_status_unknown(tmp_path):
    out = mod.status(None, str(tmp_path / "nope"),
                       str(tmp_path / "noreplace"))
    assert out["ok"] is False
    assert out["verdict"]["verdict"] == "unknown"


def test_status_empty_subsystem(tmp_path):
    lp = tmp_path / "livepatch"
    lp.mkdir()
    out = mod.status(None, str(lp), str(tmp_path / "noreplace"))
    assert out["ok"] is True
    assert out["patch_count"] == 0
    assert out["verdict"]["verdict"] == "ok"


def test_status_with_patches(tmp_path):
    lp = tmp_path / "livepatch"
    _mk_patch(lp, "kpatch_x", enabled=1, transition=0,
                signed=True)
    repl = tmp_path / "replace"
    repl.write_text("1\n")
    out = mod.status(None, str(lp), str(repl))
    assert out["ok"] is True
    assert out["patch_count"] == 1
    assert out["livepatch_replace"] == 1
    assert out["verdict"]["verdict"] == "ok"
