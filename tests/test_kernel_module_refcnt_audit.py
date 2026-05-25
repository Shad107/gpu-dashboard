"""Tests for modules/kernel_module_refcnt_audit.py R&D #95.2."""
from __future__ import annotations

import pytest

from gpu_dashboard.modules import kernel_module_refcnt_audit as mod


def _mk_module(tmp_path, name, *, refcnt=None,
                initstate="live", holders=None,
                is_builtin=False):
    d = tmp_path / "module" / name
    d.mkdir(parents=True, exist_ok=True)
    if not is_builtin:
        if refcnt is not None:
            (d / "refcnt").write_text(str(refcnt) + "\n")
        (d / "initstate").write_text(initstate + "\n")
        h = d / "holders"
        h.mkdir(exist_ok=True)
        if holders:
            for hn in holders:
                # symlink target doesn't matter for tests
                (h / hn).write_text("")
    return str(tmp_path / "module")


# --- walk_modules ----------------------------------------------

def test_walk_modules_missing(tmp_path):
    assert mod.walk_modules(str(tmp_path / "nope")) == []


def test_walk_modules_skips_builtin(tmp_path):
    _mk_module(tmp_path, "i2c_core", is_builtin=True)
    _mk_module(tmp_path, "snd", refcnt=13)
    out = mod.walk_modules(str(tmp_path / "module"))
    assert len(out) == 1
    assert out[0]["name"] == "snd"
    assert out[0]["refcnt"] == 13


def test_walk_modules_collects_holders(tmp_path):
    _mk_module(tmp_path, "snd", refcnt=5,
                  holders=["snd_pcm", "snd_seq"])
    out = mod.walk_modules(str(tmp_path / "module"))
    assert sorted(out[0]["holders"]) == [
        "snd_pcm", "snd_seq"]


# --- classify --------------------------------------------------

def _mod(*, name="m", refcnt=0, initstate="live",
         holders=None):
    return {"name": name, "refcnt": refcnt,
            "initstate": initstate,
            "holders": holders or []}


def test_classify_unknown_no_root():
    v = mod.classify([], False, False)
    assert v["verdict"] == "unknown"


def test_classify_ok_no_modules():
    v = mod.classify([], True, False)
    assert v["verdict"] == "ok"


def test_classify_requires_root_unreadable():
    v = mod.classify([], True, True)
    assert v["verdict"] == "requires_root"


def test_classify_modules_consistent():
    v = mod.classify([_mod(refcnt=5, initstate="live")],
                          True, False)
    assert v["verdict"] == "modules_consistent"


def test_classify_initstate_unloading_stuck():
    v = mod.classify(
        [_mod(name="nvidia", refcnt=3, initstate="going")],
        True, False)
    assert v["verdict"] == "initstate_unloading_stuck"


def test_classify_zero_refcnt_with_holders():
    v = mod.classify(
        [_mod(name="snd", refcnt=0,
              holders=["snd_pcm"])],
        True, False)
    assert v["verdict"] == "zero_refcnt_with_holders"


def test_classify_excessive_refcnt():
    v = mod.classify([_mod(name="snd", refcnt=80)],
                          True, False)
    assert v["verdict"] == "excessive_refcnt"


def test_classify_refcnt_at_threshold_is_ok():
    v = mod.classify([_mod(name="snd", refcnt=50)],
                          True, False)
    assert v["verdict"] == "modules_consistent"


# Priority : stuck > orphans > excessive
def test_priority_stuck_over_orphans():
    v = mod.classify([
        _mod(name="a", refcnt=2, initstate="going"),
        _mod(name="b", refcnt=0, holders=["x"]),
    ], True, False)
    assert v["verdict"] == "initstate_unloading_stuck"


def test_priority_orphans_over_excessive():
    v = mod.classify([
        _mod(name="a", refcnt=0, holders=["x"]),
        _mod(name="b", refcnt=100),
    ], True, False)
    assert v["verdict"] == "zero_refcnt_with_holders"


# --- status integration ----------------------------------------

def test_status_unknown(tmp_path):
    out = mod.status(None, str(tmp_path / "nope"))
    assert out["verdict"]["verdict"] == "unknown"


def test_status_consistent_synthetic(tmp_path):
    _mk_module(tmp_path, "snd", refcnt=5)
    _mk_module(tmp_path, "i2c_core", is_builtin=True)
    out = mod.status(None, str(tmp_path / "module"))
    assert out["verdict"]["verdict"] == "modules_consistent"
    assert out["module_count"] == 1


def test_status_excessive_refcnt_synthetic(tmp_path):
    _mk_module(tmp_path, "snd", refcnt=100)
    out = mod.status(None, str(tmp_path / "module"))
    assert out["verdict"]["verdict"] == "excessive_refcnt"
    assert out["ok"] is False
