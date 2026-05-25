"""Tests for modules/vm_numa_policy_audit.py R&D #107.1."""
from __future__ import annotations

import pytest

from gpu_dashboard.modules import vm_numa_policy_audit as mod


def test_is_multi_node_single():
    assert mod.is_multi_node("0\n") is False


def test_is_multi_node_range():
    assert mod.is_multi_node("0-1\n") is True


def test_is_multi_node_list():
    assert mod.is_multi_node("0,2-3\n") is True


def test_is_multi_node_empty():
    assert mod.is_multi_node("") is False
    assert mod.is_multi_node(None) is False


def test_classify_unknown():
    v = mod.classify(False, None, None, False)
    assert v["verdict"] == "unknown"


def test_classify_requires_root():
    v = mod.classify(True, None, None, False)
    assert v["verdict"] == "requires_root"


def test_classify_single_node_is_ok():
    # Even with bad knob values, single-node = ok
    v = mod.classify(True, 0, "Node", False)
    assert v["verdict"] == "ok"


def test_classify_numa_stat_off_warn():
    v = mod.classify(True, 0, "Default", True)
    assert v["verdict"] == "numa_stat_disabled"


def test_classify_zonelist_node_accent():
    v = mod.classify(True, 1, "Node", True)
    assert v["verdict"] == "legacy_zonelist_node"


def test_classify_ok_multi_node_sane():
    v = mod.classify(True, 1, "Default", True)
    assert v["verdict"] == "ok"


# Priority : numa_stat > zonelist
def test_priority_stat_over_zonelist():
    v = mod.classify(True, 0, "Node", True)
    assert v["verdict"] == "numa_stat_disabled"


def test_status_unknown(tmp_path):
    out = mod.status(None,
                       str(tmp_path / "nope"),
                       str(tmp_path / "no_node"))
    assert out["verdict"]["verdict"] == "unknown"


def test_status_single_node(tmp_path):
    vm = tmp_path / "vm"
    vm.mkdir()
    (vm / "numa_stat").write_text("1\n")
    (vm / "numa_zonelist_order").write_text("Node\n")
    nm = tmp_path / "online"
    nm.write_text("0\n")
    out = mod.status(None, str(vm), str(nm))
    assert out["verdict"]["verdict"] == "ok"
    assert out["multi_node"] is False


def test_status_multi_node_warn(tmp_path):
    vm = tmp_path / "vm"
    vm.mkdir()
    (vm / "numa_stat").write_text("0\n")
    (vm / "numa_zonelist_order").write_text("Default\n")
    nm = tmp_path / "online"
    nm.write_text("0-1\n")
    out = mod.status(None, str(vm), str(nm))
    assert out["verdict"]["verdict"] == "numa_stat_disabled"
