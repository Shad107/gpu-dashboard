"""Tests for modules/cgroup_tree_limits_audit.py R&D #108.4."""
from __future__ import annotations

import pytest

from gpu_dashboard.modules import cgroup_tree_limits_audit as mod


def test_to_int_or_none_max():
    assert mod._to_int_or_none("max") is None
    assert mod._to_int_or_none("max\n") is None


def test_to_int_or_none_int():
    assert mod._to_int_or_none("100\n") == 100


def test_to_int_or_none_garbage():
    assert mod._to_int_or_none(None) is None
    assert mod._to_int_or_none("garbage") is None


def test_parse_nr_descendants():
    text = (
        "nr_descendants 42\n"
        "nr_dying_descendants 0\n")
    assert mod.parse_nr_descendants(text) == 42


def test_parse_nr_descendants_empty():
    assert mod.parse_nr_descendants("") is None
    assert mod.parse_nr_descendants(None) is None


def test_classify_unknown():
    v = mod.classify(False, False, None, None, None)
    assert v["verdict"] == "unknown"


def test_classify_requires_root():
    v = mod.classify(True, False, None, None, None)
    assert v["verdict"] == "requires_root"


def test_classify_ok_unlimited():
    v = mod.classify(True, True, None, None, 100)
    assert v["verdict"] == "ok"


def test_classify_descendants_near_cap_warn():
    v = mod.classify(True, True, None, 100, 90)
    assert v["verdict"] == "cgroup_descendants_near_cap"


def test_classify_depth_low_accent():
    v = mod.classify(True, True, 3, None, 100)
    assert v["verdict"] == "cgroup_depth_capped_low"


# Priority : descendants_near_cap > depth_low
def test_priority_descendants_over_depth():
    v = mod.classify(True, True, 3, 100, 90)
    assert v["verdict"] == "cgroup_descendants_near_cap"


def test_status_unknown(tmp_path):
    out = mod.status(None, str(tmp_path / "nope"))
    assert out["verdict"]["verdict"] == "unknown"


def test_status_ok(tmp_path):
    d = tmp_path / "cgroup"
    d.mkdir()
    (d / "cgroup.controllers").write_text("cpu memory\n")
    (d / "cgroup.max.depth").write_text("max\n")
    (d / "cgroup.max.descendants").write_text("max\n")
    (d / "cgroup.stat").write_text(
        "nr_descendants 100\n"
        "nr_dying_descendants 0\n")
    out = mod.status(None, str(d))
    assert out["verdict"]["verdict"] == "ok"


def test_status_near_cap(tmp_path):
    d = tmp_path / "cgroup"
    d.mkdir()
    (d / "cgroup.controllers").write_text("cpu memory\n")
    (d / "cgroup.max.depth").write_text("max\n")
    (d / "cgroup.max.descendants").write_text("100\n")
    (d / "cgroup.stat").write_text("nr_descendants 90\n")
    out = mod.status(None, str(d))
    assert (out["verdict"]["verdict"]
            == "cgroup_descendants_near_cap")
