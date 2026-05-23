"""Tests for modules/cgroup_root_audit.py — R&D #58.1."""
from __future__ import annotations

import pytest

from gpu_dashboard.modules import cgroup_root_audit as mod


def _mk_cgroup_root(root, *, controllers="cpu memory io pids",
                       subtree="cpu memory io pids",
                       stat_text="nr_descendants 50\n",
                       v1_dirs=None):
    root.mkdir(parents=True, exist_ok=True)
    (root / "cgroup.controllers").write_text(controllers + "\n")
    (root / "cgroup.subtree_control").write_text(subtree + "\n")
    (root / "cgroup.stat").write_text(stat_text)
    (root / "cgroup.max.depth").write_text("max\n")
    (root / "cgroup.max.descendants").write_text("max\n")
    for d in v1_dirs or []:
        (root / d).mkdir(parents=True, exist_ok=True)


# --- detect_hybrid ----------------------------------------------

def test_detect_hybrid_clean(tmp_path):
    _mk_cgroup_root(tmp_path)
    assert mod.detect_hybrid(str(tmp_path)) == []


def test_detect_hybrid_v1_present(tmp_path):
    _mk_cgroup_root(tmp_path, v1_dirs=["cpu", "memory"])
    out = mod.detect_hybrid(str(tmp_path))
    assert "cpu" in out
    assert "memory" in out


def test_detect_hybrid_missing(tmp_path):
    assert mod.detect_hybrid(str(tmp_path / "nope")) == []


# --- parse_self_cgroup ------------------------------------------

def test_parse_self_cgroup_v2():
    text = "0::/user.slice/user-1000.slice/app.slice/foo.service\n"
    assert mod.parse_self_cgroup(text) == (
        "/user.slice/user-1000.slice/app.slice/foo.service")


def test_parse_self_cgroup_empty():
    assert mod.parse_self_cgroup("") == ""
    assert mod.parse_self_cgroup(None) == ""


# --- parse_cgroup_stat ------------------------------------------

def test_parse_cgroup_stat():
    text = ("nr_descendants 102\n"
              "nr_subsys_cpu 84\n"
              "nr_dying_descendants 39\n")
    out = mod.parse_cgroup_stat(text)
    assert out["nr_descendants"] == 102
    assert out["nr_dying_descendants"] == 39


# --- classify ---------------------------------------------------

def _ctrl(*names):
    return set(names)


def test_classify_unknown():
    v = mod.classify(set(), set(), [], "", {})
    assert v["verdict"] == "unknown"


def test_classify_ok():
    v = mod.classify(_ctrl("cpu", "memory", "io"),
                       _ctrl("cpu", "memory", "io"),
                       [], "/foo", {"nr_descendants": 10})
    assert v["verdict"] == "ok"


def test_classify_hybrid():
    v = mod.classify(_ctrl("cpu", "memory"),
                       _ctrl("cpu", "memory"),
                       ["cpu", "memory"], "/foo", {})
    assert v["verdict"] == "hybrid_v1_v2"


def test_classify_missing_controllers():
    v = mod.classify(_ctrl("cpu", "memory", "io", "pids"),
                       _ctrl("cpu", "pids"),
                       [], "/foo", {})
    assert v["verdict"] == "missing_controllers"


def test_classify_deep_nesting():
    deep = "/a/b/c/d/e/f/g/h"  # depth 8
    v = mod.classify(_ctrl("cpu", "memory"),
                       _ctrl("cpu", "memory"),
                       [], deep, {})
    assert v["verdict"] == "deep_nesting"


def test_classify_priority_hybrid_wins():
    v = mod.classify(_ctrl("cpu"), set(),
                       ["cpu"], "/a/b/c/d/e/f/g", {})
    assert v["verdict"] == "hybrid_v1_v2"


# --- status integration -----------------------------------------

def test_status_unknown(tmp_path):
    out = mod.status(None, str(tmp_path / "nope"),
                       str(tmp_path / "nope2"))
    assert out["ok"] is False
    assert out["verdict"]["verdict"] == "unknown"


def test_status_live_like(tmp_path):
    cg = tmp_path / "cgroup"
    _mk_cgroup_root(cg)
    sc = tmp_path / "selfcg"
    sc.write_text("0::/user.slice/user-1000.slice/app.slice/foo\n")
    out = mod.status(None, str(cg), str(sc))
    assert out["ok"] is True
    assert "memory" in out["controllers"]
    assert out["verdict"]["verdict"] == "ok"


def test_status_missing_delegate(tmp_path):
    cg = tmp_path / "cgroup"
    _mk_cgroup_root(cg, controllers="cpu memory io pids",
                       subtree="cpu pids")
    sc = tmp_path / "selfcg"
    sc.write_text("0::/foo\n")
    out = mod.status(None, str(cg), str(sc))
    assert out["verdict"]["verdict"] == "missing_controllers"
