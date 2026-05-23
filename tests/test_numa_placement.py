"""Tests for modules/numa_placement.py — R&D #35.3 NUMA audit."""
from __future__ import annotations

from pathlib import Path

import pytest

from gpu_dashboard.modules import numa_placement


def _mk_node(root: Path, n: int, *,
                cpulist: str = "0-11",
                meminfo: str | None = None,
                distance: str = "10"):
    d = root / f"node{n}"
    d.mkdir(parents=True, exist_ok=True)
    (d / "cpulist").write_text(cpulist + "\n")
    (d / "distance").write_text(distance + "\n")
    if meminfo is None:
        meminfo = (f"Node {n} MemTotal:       31794352 kB\n"
                   f"Node {n} MemFree:          587912 kB\n"
                   f"Node {n} MemUsed:        31206440 kB\n")
    (d / "meminfo").write_text(meminfo)


def _mk_proc(root: Path, pid: int, *, comm: str, cmdline: str,
                numa_maps: str = ""):
    d = root / str(pid)
    d.mkdir(parents=True, exist_ok=True)
    (d / "comm").write_text(comm + "\n")
    (d / "cmdline").write_text(cmdline.replace(" ", "\x00") + "\x00")
    if numa_maps:
        (d / "numa_maps").write_text(numa_maps)


# --- list_numa_nodes ----------------------------------------------

def test_list_nodes_single(tmp_path):
    _mk_node(tmp_path, 0)
    assert numa_placement.list_nodes(str(tmp_path)) == [0]


def test_list_nodes_multi(tmp_path):
    _mk_node(tmp_path, 0)
    _mk_node(tmp_path, 1, cpulist="12-23", distance="10 20")
    assert numa_placement.list_nodes(str(tmp_path)) == [0, 1]


def test_list_nodes_empty(tmp_path):
    assert numa_placement.list_nodes(str(tmp_path / "absent")) == []


# --- read_node helpers --------------------------------------------

def test_read_node_cpulist(tmp_path):
    _mk_node(tmp_path, 0, cpulist="0-7")
    assert numa_placement.read_node_cpulist(str(tmp_path), 0) == "0-7"


def test_read_node_meminfo(tmp_path):
    _mk_node(tmp_path, 0,
               meminfo="Node 0 MemTotal: 32000000 kB\n"
                        "Node 0 MemFree:    500000 kB\n")
    info = numa_placement.read_node_meminfo(str(tmp_path), 0)
    assert info["MemTotal_kB"] == 32000000
    assert info["MemFree_kB"] == 500000


def test_read_node_distance(tmp_path):
    _mk_node(tmp_path, 1, distance="20 10")
    assert numa_placement.read_node_distance(str(tmp_path), 1) == [20, 10]


# --- parse_numa_maps ----------------------------------------------

_SAMPLE_NUMA_MAPS = """\
64eed5228000 default file=/llama-server
64eed5252000 default file=/llama-server mapped=487 active=0 N0=487 kernelpagesize_kB=4
64eed5663000 default file=/llama-server mapped=31 active=0 N0=31 kernelpagesize_kB=4
64eed572d000 default file=/x anon=9 dirty=9 active=0 N0=9 kernelpagesize_kB=4
"""


def test_parse_numa_maps_counts_pages_per_node():
    counts = numa_placement.parse_numa_maps(_SAMPLE_NUMA_MAPS)
    # 487 + 31 + 9 = 527 pages on N0
    assert counts[0] == 527


def test_parse_numa_maps_multi_node():
    txt = ("64aa default file=/x N0=100 N1=200\n"
           "64bb default file=/y N0=50\n"
           "64cc default file=/z N1=300\n")
    counts = numa_placement.parse_numa_maps(txt)
    assert counts[0] == 150
    assert counts[1] == 500


def test_parse_numa_maps_empty():
    assert numa_placement.parse_numa_maps("") == {}


def test_parse_numa_maps_no_node_pages():
    # No N<n>= tokens → empty dict
    txt = "64aa default file=/x mapped=10\n"
    assert numa_placement.parse_numa_maps(txt) == {}


# --- classify -----------------------------------------------------

def test_classify_single_node():
    v = numa_placement.classify(node_count=1, pid_counts=[])
    assert v["verdict"] == "single_node"


def test_classify_balanced_when_all_on_one_node():
    # Multi-node host but daemon mem entirely on node 0 → ok
    v = numa_placement.classify(node_count=2,
                                  pid_counts=[{"pid": 100, "comm": "llama-server",
                                                  "per_node": {0: 1000000, 1: 0}}])
    assert v["verdict"] == "balanced"


def test_classify_cross_node_split_warns():
    # 60/40 split across two NUMA nodes
    v = numa_placement.classify(node_count=2,
                                  pid_counts=[{"pid": 100, "comm": "llama-server",
                                                  "per_node": {0: 600000, 1: 400000}}])
    assert v["verdict"] == "cross_node_split"
    assert "numactl" in v["recommendation"].lower() or "numa" in v["recommendation"].lower()


def test_classify_unknown_no_data():
    v = numa_placement.classify(node_count=0, pid_counts=[])
    assert v["verdict"] == "unknown"


def test_classify_picks_worst_across_pids():
    pids = [
        {"pid": 1, "comm": "ollama",
         "per_node": {0: 100, 1: 0}},
        {"pid": 2, "comm": "llama-server",
         "per_node": {0: 600, 1: 400}},
    ]
    v = numa_placement.classify(node_count=2, pid_counts=pids)
    assert v["verdict"] == "cross_node_split"


# --- status ------------------------------------------------------

def test_status_no_numa_node_dir(tmp_path, monkeypatch):
    monkeypatch.setattr(numa_placement, "_NODE_ROOT",
                          str(tmp_path / "absent"))
    monkeypatch.setattr(numa_placement, "_PROC", str(tmp_path / "proc"))
    s = numa_placement.status()
    assert s["ok"] is False
    assert s["error"] == "numa_unavailable"


def test_status_single_node_live_case(tmp_path, monkeypatch):
    nroot = tmp_path / "node"
    proot = tmp_path / "proc"
    _mk_node(nroot, 0)
    _mk_proc(proot, 1234, comm="llama-server",
             cmdline="/llama-server --model x.gguf",
             numa_maps=_SAMPLE_NUMA_MAPS)
    monkeypatch.setattr(numa_placement, "_NODE_ROOT", str(nroot))
    monkeypatch.setattr(numa_placement, "_PROC", str(proot))
    s = numa_placement.status()
    assert s["ok"] is True
    assert s["node_count"] == 1
    assert s["verdict"]["verdict"] == "single_node"


def test_status_dual_node_balanced(tmp_path, monkeypatch):
    nroot = tmp_path / "node"
    proot = tmp_path / "proc"
    _mk_node(nroot, 0)
    _mk_node(nroot, 1, cpulist="12-23")
    _mk_proc(proot, 1234, comm="llama-server",
             cmdline="/llama-server",
             numa_maps="64aa default file=/x N0=10000\n")
    monkeypatch.setattr(numa_placement, "_NODE_ROOT", str(nroot))
    monkeypatch.setattr(numa_placement, "_PROC", str(proot))
    s = numa_placement.status()
    assert s["node_count"] == 2
    assert s["verdict"]["verdict"] == "balanced"


def test_status_dual_node_split_warns(tmp_path, monkeypatch):
    nroot = tmp_path / "node"
    proot = tmp_path / "proc"
    _mk_node(nroot, 0)
    _mk_node(nroot, 1, cpulist="12-23")
    _mk_proc(proot, 1234, comm="llama-server",
             cmdline="/llama-server",
             numa_maps="64aa default file=/x N0=600 N1=400\n")
    monkeypatch.setattr(numa_placement, "_NODE_ROOT", str(nroot))
    monkeypatch.setattr(numa_placement, "_PROC", str(proot))
    s = numa_placement.status()
    assert s["verdict"]["verdict"] == "cross_node_split"


def test_status_includes_node_summary(tmp_path, monkeypatch):
    nroot = tmp_path / "node"
    proot = tmp_path / "proc"
    proot.mkdir()
    _mk_node(nroot, 0, cpulist="0-11",
               meminfo="Node 0 MemTotal: 32000000 kB\n"
                        "Node 0 MemFree:   500000 kB\n")
    monkeypatch.setattr(numa_placement, "_NODE_ROOT", str(nroot))
    monkeypatch.setattr(numa_placement, "_PROC", str(proot))
    s = numa_placement.status()
    node = s["nodes"][0]
    assert node["id"] == 0
    assert node["cpu_list"] == "0-11"
    assert node["mem_total_kb"] == 32000000
    assert node["mem_free_kb"] == 500000


def test_is_llm_proc_matches_llama_server():
    assert numa_placement.is_llm_proc(
        "llama-server", "/llama.cpp/llama-server --model x.gguf")
