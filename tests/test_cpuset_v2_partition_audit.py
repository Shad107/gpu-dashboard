"""Tests for modules/cpuset_v2_partition_audit.py — R&D #96.2."""
from __future__ import annotations

import pytest

from gpu_dashboard.modules import cpuset_v2_partition_audit as mod


def _mk_root(tmp_path, *, controllers="cpuset cpu memory"):
    d = tmp_path / "cgroup"
    d.mkdir(parents=True, exist_ok=True)
    (d / "cgroup.controllers").write_text(controllers + "\n")
    return str(d)


def _mk_leaf(tmp_path, path, *, requested="",
              effective="0-11", partition="member"):
    d = tmp_path / "cgroup" / path
    d.mkdir(parents=True, exist_ok=True)
    (d / "cpuset.cpus").write_text(requested + "\n")
    (d / "cpuset.cpus.effective").write_text(
        effective + "\n")
    (d / "cpuset.cpus.partition").write_text(
        partition + "\n")


# --- parse_cpu_list --------------------------------------------

def test_parse_empty():
    assert mod.parse_cpu_list("") == set()
    assert mod.parse_cpu_list(None) == set()


def test_parse_range_and_single():
    assert mod.parse_cpu_list("0-3,8,10-11") == {
        0, 1, 2, 3, 8, 10, 11}


def test_parse_garbage_skipped():
    assert mod.parse_cpu_list("0-3,zz,5") == {
        0, 1, 2, 3, 5}


# --- controller_present ----------------------------------------

def test_controller_present_missing(tmp_path):
    assert mod.controller_present(
        str(tmp_path / "nope")) is None


def test_controller_present_yes(tmp_path):
    r = _mk_root(tmp_path)
    assert mod.controller_present(r) is True


def test_controller_present_no(tmp_path):
    r = _mk_root(tmp_path, controllers="cpu memory")
    assert mod.controller_present(r) is False


# --- walk_cgroups ----------------------------------------------

def test_walk_skips_default_inherit(tmp_path):
    _mk_root(tmp_path)
    _mk_leaf(tmp_path, "user.slice",
                 requested="")  # default
    out = mod.walk_cgroups(str(tmp_path / "cgroup"))
    assert out == []


def test_walk_finds_explicit_request(tmp_path):
    _mk_root(tmp_path)
    _mk_leaf(tmp_path, "user.slice/leaf",
                 requested="0-3", effective="0-3")
    out = mod.walk_cgroups(str(tmp_path / "cgroup"))
    assert len(out) == 1
    assert out[0]["requested"] == {0, 1, 2, 3}


# --- classify --------------------------------------------------

def _cg(*, path="x", requested=None, effective=None,
        partition="member"):
    return {"path": path,
            "requested": requested or set(),
            "effective": effective or set(),
            "partition": partition}


def test_classify_requires_root():
    v = mod.classify(None, [])
    assert v["verdict"] == "requires_root"


def test_classify_unknown_no_cpuset():
    v = mod.classify(False, [])
    assert v["verdict"] == "unknown"


def test_classify_sane_no_cgroups():
    v = mod.classify(True, [])
    assert v["verdict"] == "cpuset_sane"


def test_classify_effective_empty():
    v = mod.classify(True, [
        _cg(path="user/leaf",
            requested={0, 1, 2, 3}, effective=set())])
    assert v["verdict"] == "cpuset_effective_empty"


def test_classify_partition_invalid():
    v = mod.classify(True, [
        _cg(path="user/leaf",
            requested={0, 1, 2, 3}, effective={0, 1, 2, 3},
            partition="root invalid")])
    assert v["verdict"] == "partition_invalid"


def test_classify_cpuset_drift():
    v = mod.classify(True, [
        _cg(path="user/leaf",
            requested={0, 1, 2, 3, 4},
            effective={0, 1, 2, 3})])
    assert v["verdict"] == "cpuset_drift"


def test_classify_sane_match():
    v = mod.classify(True, [
        _cg(path="user/leaf",
            requested={0, 1, 2, 3},
            effective={0, 1, 2, 3})])
    assert v["verdict"] == "cpuset_sane"


# Priority : effective_empty > partition_invalid > drift
def test_priority_empty_over_invalid():
    v = mod.classify(True, [
        _cg(path="a", requested={0}, effective=set(),
            partition="root invalid"),
    ])
    assert v["verdict"] == "cpuset_effective_empty"


def test_priority_invalid_over_drift():
    v = mod.classify(True, [
        _cg(path="a", requested={0, 1, 2},
            effective={0, 1}, partition="root invalid"),
    ])
    assert v["verdict"] == "partition_invalid"


# --- status integration ----------------------------------------

def test_status_requires_root(tmp_path):
    out = mod.status(None, str(tmp_path / "nope"))
    assert out["verdict"]["verdict"] == "requires_root"


def test_status_sane_synthetic(tmp_path):
    _mk_root(tmp_path)
    _mk_leaf(tmp_path, "user.slice")  # default inherit
    out = mod.status(None, str(tmp_path / "cgroup"))
    assert out["verdict"]["verdict"] == "cpuset_sane"


def test_status_effective_empty_synthetic(tmp_path):
    _mk_root(tmp_path)
    _mk_leaf(tmp_path, "user.slice/leaf",
                 requested="0-3", effective="")
    out = mod.status(None, str(tmp_path / "cgroup"))
    assert (out["verdict"]["verdict"]
            == "cpuset_effective_empty")
    assert out["ok"] is False
