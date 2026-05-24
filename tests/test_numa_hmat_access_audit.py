"""Tests for modules/numa_hmat_access_audit.py — R&D #76.3."""
from __future__ import annotations

import pytest

from gpu_dashboard.modules import numa_hmat_access_audit as mod


def _mk_node(root, node_id):
    d = root / f"node{node_id}"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _mk_access(root, node_id, access_idx, *,
                   read_bandwidth=100000, read_latency=100,
                   write_bandwidth=80000, write_latency=120):
    d = root / f"node{node_id}" / f"access{access_idx}"
    d.mkdir(parents=True, exist_ok=True)
    (d / "read_bandwidth").write_text(f"{read_bandwidth}\n")
    (d / "read_latency").write_text(f"{read_latency}\n")
    (d / "write_bandwidth").write_text(f"{write_bandwidth}\n")
    (d / "write_latency").write_text(f"{write_latency}\n")


# --- list_numa_nodes -------------------------------------------

def test_list_nodes_missing(tmp_path):
    assert mod.list_numa_nodes(str(tmp_path / "nope")) == []


def test_list_nodes(tmp_path):
    _mk_node(tmp_path, 0)
    _mk_node(tmp_path, 1)
    (tmp_path / "has_cpu").write_text("0-3\n")  # non-node entry
    out = mod.list_numa_nodes(str(tmp_path))
    assert out == [0, 1]


# --- read_access -----------------------------------------------

def test_read_access_missing(tmp_path):
    _mk_node(tmp_path, 0)
    out = mod.read_access(str(tmp_path), 0, 0)
    assert out == {"present": False}


def test_read_access(tmp_path):
    _mk_access(tmp_path, 0, 0, read_bandwidth=120000,
                   read_latency=80)
    out = mod.read_access(str(tmp_path), 0, 0)
    assert out["present"] is True
    assert out["read_bandwidth"] == 120000
    assert out["read_latency"] == 80


# --- classify ---------------------------------------------------

def test_classify_unknown():
    v = mod.classify(False, 0, {})
    assert v["verdict"] == "unknown"


def test_classify_single_node():
    v = mod.classify(True, 1, {})
    assert v["verdict"] == "single_node_uniform"


def test_classify_ok():
    # Two nodes, both with access0 + access1 with sane uniform
    # bandwidth and tight latency stddev.
    acc = {
        0: {0: {"present": True, "read_bandwidth": 100000,
                  "read_latency": 100,
                  "write_bandwidth": 80000,
                  "write_latency": 120},
              1: {"present": True, "read_bandwidth": 80000,
                    "read_latency": 130,
                    "write_bandwidth": 70000,
                    "write_latency": 150}},
        1: {0: {"present": True, "read_bandwidth": 100000,
                  "read_latency": 105,
                  "write_bandwidth": 80000,
                  "write_latency": 125},
              1: {"present": True, "read_bandwidth": 80000,
                    "read_latency": 135,
                    "write_bandwidth": 70000,
                    "write_latency": 155}},
    }
    v = mod.classify(True, 2, acc)
    assert v["verdict"] == "ok"


def test_classify_bw_cliff():
    # access1 bandwidth is 1/4 of access0 on node0
    acc = {
        0: {0: {"present": True, "read_bandwidth": 100000,
                  "read_latency": 100, "write_bandwidth": 80000,
                  "write_latency": 120},
              1: {"present": True, "read_bandwidth": 25000,
                    "read_latency": 200, "write_bandwidth": 20000,
                    "write_latency": 250}},
        1: {0: {"present": True, "read_bandwidth": 100000,
                  "read_latency": 100, "write_bandwidth": 80000,
                  "write_latency": 120},
              1: {"present": True, "read_bandwidth": 80000,
                    "read_latency": 130, "write_bandwidth": 70000,
                    "write_latency": 150}},
    }
    v = mod.classify(True, 2, acc)
    assert v["verdict"] == "cross_node_bw_cliff"


def test_classify_asymmetric_latency():
    # node0 has latency=80, node1 has latency=200 → stddev/mean
    # ~43 %.
    acc = {
        0: {0: {"present": True, "read_bandwidth": 100000,
                  "read_latency": 80, "write_bandwidth": 80000,
                  "write_latency": 100},
              1: {"present": True, "read_bandwidth": 80000,
                    "read_latency": 100, "write_bandwidth": 70000,
                    "write_latency": 120}},
        1: {0: {"present": True, "read_bandwidth": 100000,
                  "read_latency": 200, "write_bandwidth": 80000,
                  "write_latency": 220},
              1: {"present": True, "read_bandwidth": 80000,
                    "read_latency": 250, "write_bandwidth": 70000,
                    "write_latency": 270}},
    }
    v = mod.classify(True, 2, acc)
    assert v["verdict"] == "asymmetric_latency"


def test_classify_hmat_absent():
    # Multi-node but no access dirs
    v = mod.classify(True, 2, {0: {}, 1: {}})
    assert v["verdict"] == "hmat_absent"


# Priority : bw_cliff > asymmetric > hmat_absent
def test_priority_bw_cliff_over_asym():
    # Both bw cliff AND asymmetric latency present
    acc = {
        0: {0: {"present": True, "read_bandwidth": 100000,
                  "read_latency": 80, "write_bandwidth": 80000,
                  "write_latency": 100},
              1: {"present": True, "read_bandwidth": 25000,
                    "read_latency": 200, "write_bandwidth": 20000,
                    "write_latency": 250}},
        1: {0: {"present": True, "read_bandwidth": 100000,
                  "read_latency": 200, "write_bandwidth": 80000,
                  "write_latency": 220},
              1: {"present": True, "read_bandwidth": 80000,
                    "read_latency": 250, "write_bandwidth": 70000,
                    "write_latency": 270}},
    }
    v = mod.classify(True, 2, acc)
    assert v["verdict"] == "cross_node_bw_cliff"


# --- status integration -----------------------------------------

def test_status_unknown(tmp_path):
    out = mod.status(None, str(tmp_path / "nope"))
    assert out["ok"] is False
    assert out["verdict"]["verdict"] == "unknown"


def test_status_single_node(tmp_path):
    _mk_node(tmp_path, 0)
    out = mod.status(None, str(tmp_path))
    assert out["ok"] is True
    assert out["node_count"] == 1
    assert out["verdict"]["verdict"] == "single_node_uniform"


def test_status_bw_cliff_synthetic(tmp_path):
    _mk_node(tmp_path, 0)
    _mk_node(tmp_path, 1)
    _mk_access(tmp_path, 0, 0, read_bandwidth=100000,
                   read_latency=100)
    _mk_access(tmp_path, 0, 1, read_bandwidth=25000,
                   read_latency=200)
    _mk_access(tmp_path, 1, 0, read_bandwidth=100000,
                   read_latency=100)
    _mk_access(tmp_path, 1, 1, read_bandwidth=80000,
                   read_latency=130)
    out = mod.status(None, str(tmp_path))
    assert out["verdict"]["verdict"] == "cross_node_bw_cliff"
