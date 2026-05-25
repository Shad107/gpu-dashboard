"""Tests for modules/cgroup_delegate_audit.py — R&D #97.3."""
from __future__ import annotations

import pytest

from gpu_dashboard.modules import cgroup_delegate_audit as mod


def _mk_v2_root(tmp_path,
                  controllers="cpuset cpu io memory pids"):
    d = tmp_path / "cgroup"
    d.mkdir(parents=True, exist_ok=True)
    (d / "cgroup.controllers").write_text(controllers + "\n")
    return str(d)


def _mk_slice(tmp_path, path, *, subtree="cpu io memory pids",
               freeze="0", events="populated 0\nfrozen 0\n",
               procs=""):
    d = tmp_path / "cgroup" / path
    d.mkdir(parents=True, exist_ok=True)
    (d / "cgroup.subtree_control").write_text(subtree + "\n")
    (d / "cgroup.freeze").write_text(freeze + "\n")
    (d / "cgroup.events").write_text(events)
    (d / "cgroup.procs").write_text(procs)


# --- _parse_events ---------------------------------------------

def test_parse_events_empty():
    assert mod._parse_events("") == {}


def test_parse_events_typical():
    out = mod._parse_events(
        "populated 1\nfrozen 0\n")
    assert out == {"populated": 1, "frozen": 0}


# --- find_user_manager_paths -----------------------------------

def test_find_user_manager_paths(tmp_path):
    _mk_v2_root(tmp_path)
    d = tmp_path / "cgroup" / "user.slice" / "user-1000.slice"
    d.mkdir(parents=True)
    (d / "user@1000.service").mkdir()
    out = mod.find_user_manager_paths(
        str(tmp_path / "cgroup"))
    assert len(out) == 1
    assert out[0].endswith("user@1000.service")


# --- walk_slices -----------------------------------------------

def test_walk_slices_finds_system(tmp_path):
    _mk_v2_root(tmp_path)
    _mk_slice(tmp_path, "system.slice",
                 subtree="cpu io memory")
    out = mod.walk_slices(str(tmp_path / "cgroup"))
    assert len(out) == 1
    assert out[0]["path"] == "system.slice"
    assert "io" in out[0]["controllers"]


# --- classify --------------------------------------------------

def _slice(*, path="system.slice",
           controllers=None, freeze=0,
           events=None, proc_count=0, child_count=0):
    return {"path": path,
            "controllers": controllers
            or ["cpu", "io", "memory", "pids"],
            "freeze": freeze,
            "events": events or {"populated": 0,
                                  "frozen": 0},
            "proc_count": proc_count,
            "child_count": child_count}


def test_classify_unknown_no_v2():
    v = mod.classify(False, False, [], True)
    assert v["verdict"] == "unknown"


def test_classify_requires_root():
    v = mod.classify(True, True, [], True)
    assert v["verdict"] == "requires_root"


def test_classify_ok():
    v = mod.classify(True, False,
                          [_slice(proc_count=5)], True)
    assert v["verdict"] == "ok"


def test_classify_frozen_err():
    v = mod.classify(True, False,
                          [_slice(freeze=1)], True)
    assert v["verdict"] == "frozen_descendant"


def test_classify_zombie_warn():
    v = mod.classify(
        True, False,
        [_slice(events={"populated": 1, "frozen": 0},
                proc_count=0)],
        True)
    assert v["verdict"] == "populated_but_no_procs"


def test_classify_missing_io_accent():
    v = mod.classify(
        True, False,
        [_slice(controllers=["cpu", "memory", "pids"])],
        True)
    assert v["verdict"] == "subtree_missing_io"


def test_classify_populated_with_children_is_ok():
    # Parent slice has populated=1 but procs are in
    # child cgroups (scopes/services) — totally normal.
    v = mod.classify(
        True, False,
        [_slice(events={"populated": 1, "frozen": 0},
                proc_count=0, child_count=5)],
        True)
    assert v["verdict"] == "ok"


def test_classify_delegate_missing_accent():
    v = mod.classify(True, False,
                          [_slice(proc_count=5)],
                          False)
    assert v["verdict"] == "delegate_file_missing"


# Priority : frozen > zombie > missing_io > delegate
def test_priority_frozen_over_zombie():
    v = mod.classify(
        True, False,
        [_slice(freeze=1,
                events={"populated": 1, "frozen": 0},
                proc_count=0)],
        True)
    assert v["verdict"] == "frozen_descendant"


def test_priority_zombie_over_missing_io():
    v = mod.classify(
        True, False,
        [_slice(controllers=["cpu", "memory"],
                events={"populated": 1, "frozen": 0},
                proc_count=0)],
        True)
    assert v["verdict"] == "populated_but_no_procs"


def test_priority_missing_io_over_delegate():
    v = mod.classify(
        True, False,
        [_slice(controllers=["cpu", "memory"],
                proc_count=5)],
        False)
    assert v["verdict"] == "subtree_missing_io"


# --- status integration ----------------------------------------

def test_status_unknown(tmp_path):
    out = mod.status(None,
                       str(tmp_path / "nope_cg"),
                       str(tmp_path / "nope_delegate"))
    assert out["verdict"]["verdict"] == "unknown"


def test_status_ok_synthetic(tmp_path):
    _mk_v2_root(tmp_path)
    _mk_slice(tmp_path, "system.slice",
                 procs="1234\n5678\n")
    (tmp_path / "delegate").write_text("cgroup.procs\n")
    out = mod.status(None, str(tmp_path / "cgroup"),
                       str(tmp_path / "delegate"))
    assert out["verdict"]["verdict"] == "ok"


def test_status_missing_io_synthetic(tmp_path):
    _mk_v2_root(tmp_path)
    _mk_slice(tmp_path, "system.slice",
                 subtree="cpu memory pids",
                 procs="1234\n")
    (tmp_path / "delegate").write_text("ok")
    out = mod.status(None, str(tmp_path / "cgroup"),
                       str(tmp_path / "delegate"))
    assert out["verdict"]["verdict"] == "subtree_missing_io"
    assert out["ok"] is False
