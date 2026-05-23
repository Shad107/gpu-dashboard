"""Tests for modules/numa_topology_audit.py — R&D #55.2."""
from __future__ import annotations

import pytest

from gpu_dashboard.modules import numa_topology_audit as mod


def _mk_node(root, idx, *, distance="10", cpulist="0-11",
              numastat_text=None):
    d = root / f"node{idx}"
    d.mkdir(parents=True, exist_ok=True)
    (d / "distance").write_text(distance + "\n")
    (d / "cpulist").write_text(cpulist + "\n")
    if numastat_text:
        (d / "numastat").write_text(numastat_text)
    return d


def _mk_gpu(root, bdf, *, numa_node=-1,
              local_cpulist="0-11"):
    d = root / bdf
    d.mkdir(parents=True, exist_ok=True)
    (d / "vendor").write_text("0x10de\n")
    (d / "class").write_text("0x030000\n")
    (d / "numa_node").write_text(f"{numa_node}\n")
    (d / "local_cpulist").write_text(local_cpulist + "\n")


# --- parse_numastat ---------------------------------------------

def test_parse_numastat_basic():
    text = ("numa_hit             1000000\n"
              "numa_miss              50000\n"
              "numa_foreign           50000\n")
    out = mod.parse_numastat(text)
    assert out["numa_hit"] == 1000000
    assert out["numa_miss"] == 50000


def test_parse_numastat_empty():
    assert mod.parse_numastat("") == {}
    assert mod.parse_numastat(None) == {}


# --- list_nodes -------------------------------------------------

def test_list_nodes_missing(tmp_path):
    assert mod.list_nodes(str(tmp_path / "nope")) == []


def test_list_nodes(tmp_path):
    _mk_node(tmp_path, 0, distance="10 20")
    _mk_node(tmp_path, 1, distance="20 10")
    out = mod.list_nodes(str(tmp_path))
    assert len(out) == 2
    assert out[0]["distance"] == ["10", "20"]


# --- list_nvidia_gpus -------------------------------------------

def test_list_nvidia_gpus_missing(tmp_path):
    assert mod.list_nvidia_gpus(str(tmp_path / "nope")) == []


def test_list_nvidia_gpus(tmp_path):
    _mk_gpu(tmp_path, "0000:01:00.0", numa_node=-1)
    _mk_gpu(tmp_path, "0000:02:00.0", numa_node=0)
    # Add a non-GPU NVIDIA device
    d = tmp_path / "0000:01:00.1"
    d.mkdir()
    (d / "vendor").write_text("0x10de\n")
    (d / "class").write_text("0x040300\n")  # audio
    out = mod.list_nvidia_gpus(str(tmp_path))
    assert len(out) == 2
    assert sorted(g["bdf"] for g in out) == [
        "0000:01:00.0", "0000:02:00.0"]


# --- classify ---------------------------------------------------

def _node(id_=0, distance=None, hit=1000000, miss=0):
    return {"id": id_,
              "distance": distance or ["10"],
              "cpulist": "0-11",
              "numastat": {"numa_hit": hit, "numa_miss": miss,
                              "numa_foreign": miss}}


def _gpu(bdf="0000:01:00.0", numa=-1):
    return {"bdf": bdf, "numa_node": numa,
              "local_cpulist": "0-11"}


def test_classify_unknown():
    v = mod.classify([], 1, [])
    assert v["verdict"] == "unknown"


def test_classify_single_node():
    v = mod.classify([_node()], 1, [_gpu()])
    assert v["verdict"] == "single_node"


def test_classify_ok_multi_node():
    v = mod.classify([_node(0), _node(1)], 1, [_gpu(numa=0)])
    assert v["verdict"] == "ok"


def test_classify_gpu_numa_unset_multi_node():
    v = mod.classify([_node(0), _node(1)], 1,
                       [_gpu(numa=-1)])
    assert v["verdict"] == "gpu_numa_unset"


def test_classify_gpu_numa_unset_single_node_falls_through():
    # On single-node host, gpu_numa=-1 is informational, not a
    # priority verdict.
    v = mod.classify([_node()], 1, [_gpu(numa=-1)])
    assert v["verdict"] == "single_node"


def test_classify_cross_node_memory():
    nodes = [_node(0, hit=900_000, miss=200_000),
              _node(1, hit=1_000_000, miss=0)]
    v = mod.classify(nodes, 1, [_gpu(numa=0)])
    assert v["verdict"] == "cross_node_memory"


def test_classify_balancing_off_multi_node():
    v = mod.classify([_node(0), _node(1)], 0, [_gpu(numa=0)])
    assert v["verdict"] == "balancing_off_on_multi_node"


def test_classify_priority_gpu_unset_wins():
    nodes = [_node(0, hit=900_000, miss=200_000),
              _node(1)]
    v = mod.classify(nodes, 0, [_gpu(numa=-1)])
    assert v["verdict"] == "gpu_numa_unset"


# --- status integration -----------------------------------------

def test_status_unknown(tmp_path):
    out = mod.status(None, str(tmp_path / "nope"),
                       str(tmp_path / "nobal"),
                       str(tmp_path / "nopci"))
    assert out["ok"] is False
    assert out["verdict"]["verdict"] == "unknown"


def test_status_single_node(tmp_path):
    sn = tmp_path / "node"
    _mk_node(sn, 0)
    bal = tmp_path / "balancing"
    bal.write_text("0\n")
    pci = tmp_path / "pci"
    _mk_gpu(pci, "0000:01:00.0", numa_node=-1)
    out = mod.status(None, str(sn), str(bal), str(pci))
    assert out["ok"] is True
    assert out["node_count"] == 1
    assert out["verdict"]["verdict"] == "single_node"
