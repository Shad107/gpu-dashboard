"""Tests for modules/cpu_cache_topology.py — R&D #37.4 L3 cache topology."""
from __future__ import annotations

from pathlib import Path

import pytest

from gpu_dashboard.modules import cpu_cache_topology


def _mk_cpu_cache(root: Path, cpu_id: int, indices: list):
    """indices = [{"level": "1", "size": "32K", "shared_cpu_list": "0",
                   "type": "Data"}, ...]"""
    base = root / f"cpu{cpu_id}" / "cache"
    base.mkdir(parents=True, exist_ok=True)
    for i, idx in enumerate(indices):
        d = base / f"index{i}"
        d.mkdir(exist_ok=True)
        for k, v in idx.items():
            (d / k).write_text(v + "\n")


def _vm_topology(root: Path, n_cpus: int = 12, l3_size: str = "16384K"):
    """Live-rig style: every CPU has L1/L2 private, L3 shared across all."""
    shared_l3 = f"0-{n_cpus - 1}"
    for i in range(n_cpus):
        _mk_cpu_cache(root, i, [
            {"level": "1", "size": "32K", "shared_cpu_list": str(i),
             "type": "Data"},
            {"level": "1", "size": "32K", "shared_cpu_list": str(i),
             "type": "Instruction"},
            {"level": "2", "size": "4096K", "shared_cpu_list": str(i),
             "type": "Unified"},
            {"level": "3", "size": l3_size, "shared_cpu_list": shared_l3,
             "type": "Unified"},
        ])


def _dual_ccd_topology(root: Path):
    """Synthetic 16-core dual-CCD AMD: 8 cores share L3 #0, 8 share L3 #1."""
    for i in range(16):
        l3_share = "0-7" if i < 8 else "8-15"
        _mk_cpu_cache(root, i, [
            {"level": "1", "size": "32K", "shared_cpu_list": str(i),
             "type": "Data"},
            {"level": "3", "size": "32768K", "shared_cpu_list": l3_share,
             "type": "Unified"},
        ])


# --- parse_size_bytes -------------------------------------------

def test_parse_size_bytes_kib():
    assert cpu_cache_topology.parse_size_bytes("32K") == 32 * 1024


def test_parse_size_bytes_mib():
    assert cpu_cache_topology.parse_size_bytes("16M") == 16 * 1024 * 1024


def test_parse_size_bytes_bare():
    assert cpu_cache_topology.parse_size_bytes("4096") == 4096


def test_parse_size_bytes_empty():
    assert cpu_cache_topology.parse_size_bytes("") is None
    assert cpu_cache_topology.parse_size_bytes(None) is None


# --- read_cache_indices ----------------------------------------

def test_read_cache_indices(tmp_path):
    _mk_cpu_cache(tmp_path, 0, [
        {"level": "1", "size": "32K", "shared_cpu_list": "0",
         "type": "Data"},
        {"level": "3", "size": "16384K", "shared_cpu_list": "0-11",
         "type": "Unified"},
    ])
    indices = cpu_cache_topology.read_cache_indices(str(tmp_path), 0)
    assert len(indices) == 2
    assert indices[0]["level"] == 1
    assert indices[1]["shared_cpu_list"] == "0-11"


def test_read_cache_indices_no_cache_dir(tmp_path):
    assert cpu_cache_topology.read_cache_indices(str(tmp_path), 0) == []


# --- extract_l3_islands ---------------------------------------

def test_extract_l3_islands_single_island(tmp_path):
    _vm_topology(tmp_path, n_cpus=12)
    islands = cpu_cache_topology.extract_l3_islands(str(tmp_path))
    assert len(islands) == 1
    assert islands[0]["cpu_list"] == "0-11"
    assert islands[0]["cpus"] == list(range(12))


def test_extract_l3_islands_dual_ccd(tmp_path):
    _dual_ccd_topology(tmp_path)
    islands = cpu_cache_topology.extract_l3_islands(str(tmp_path))
    assert len(islands) == 2
    cpu_lists = sorted(i["cpu_list"] for i in islands)
    assert cpu_lists == ["0-7", "8-15"]


def test_extract_l3_islands_no_l3(tmp_path):
    # Only L1/L2 caches
    _mk_cpu_cache(tmp_path, 0, [
        {"level": "1", "size": "32K", "shared_cpu_list": "0",
         "type": "Data"},
    ])
    assert cpu_cache_topology.extract_l3_islands(str(tmp_path)) == []


# --- classify --------------------------------------------------

def test_classify_single_l3():
    islands = [{"cpu_list": "0-11", "cpus": list(range(12)),
                "size_bytes": 16 * 1024 * 1024}]
    v = cpu_cache_topology.classify(islands, total_cpus=12)
    assert v["verdict"] == "single_l3"


def test_classify_multi_l3_dual_ccd():
    islands = [
        {"cpu_list": "0-7", "cpus": list(range(8)),
         "size_bytes": 32 * 1024 * 1024},
        {"cpu_list": "8-15", "cpus": list(range(8, 16)),
         "size_bytes": 32 * 1024 * 1024},
    ]
    v = cpu_cache_topology.classify(islands, total_cpus=16)
    assert v["verdict"] == "multi_l3_islands"
    assert "0-7" in v["recommendation"]
    assert "CPUAffinity" in v["recommendation"]


def test_classify_no_l3():
    v = cpu_cache_topology.classify([], total_cpus=8)
    assert v["verdict"] == "no_l3"


def test_classify_recipe_per_island_pinning():
    islands = [
        {"cpu_list": "0-7", "cpus": list(range(8)), "size_bytes": 32 << 20},
        {"cpu_list": "8-15", "cpus": list(range(8, 16)), "size_bytes": 32 << 20},
    ]
    v = cpu_cache_topology.classify(islands, total_cpus=16)
    # Recipe should mention the largest island
    rec = v["recommendation"]
    assert "taskset" in rec or "CPUAffinity" in rec
    assert "0-7" in rec  # pin to first island


# --- status ---------------------------------------------------

def test_status_live_single_l3(tmp_path, monkeypatch):
    # The live-rig case
    _vm_topology(tmp_path, n_cpus=12)
    online = tmp_path / "online"
    online.write_text("0-11\n")
    monkeypatch.setattr(cpu_cache_topology, "_CPU_ROOT", str(tmp_path))
    monkeypatch.setattr(cpu_cache_topology, "_CPU_ONLINE", str(online))
    s = cpu_cache_topology.status()
    assert s["ok"] is True
    assert s["total_cpus"] == 12
    assert s["l3_island_count"] == 1
    assert s["verdict"]["verdict"] == "single_l3"


def test_status_dual_ccd(tmp_path, monkeypatch):
    _dual_ccd_topology(tmp_path)
    online = tmp_path / "online"
    online.write_text("0-15\n")
    monkeypatch.setattr(cpu_cache_topology, "_CPU_ROOT", str(tmp_path))
    monkeypatch.setattr(cpu_cache_topology, "_CPU_ONLINE", str(online))
    s = cpu_cache_topology.status()
    assert s["l3_island_count"] == 2
    assert s["verdict"]["verdict"] == "multi_l3_islands"


def test_status_no_l3_cache(tmp_path, monkeypatch):
    _mk_cpu_cache(tmp_path, 0, [
        {"level": "1", "size": "32K", "shared_cpu_list": "0",
         "type": "Data"},
    ])
    online = tmp_path / "online"
    online.write_text("0\n")
    monkeypatch.setattr(cpu_cache_topology, "_CPU_ROOT", str(tmp_path))
    monkeypatch.setattr(cpu_cache_topology, "_CPU_ONLINE", str(online))
    s = cpu_cache_topology.status()
    assert s["verdict"]["verdict"] == "no_l3"


def test_status_includes_l1_l2_summary(tmp_path, monkeypatch):
    _vm_topology(tmp_path, n_cpus=4)
    online = tmp_path / "online"
    online.write_text("0-3\n")
    monkeypatch.setattr(cpu_cache_topology, "_CPU_ROOT", str(tmp_path))
    monkeypatch.setattr(cpu_cache_topology, "_CPU_ONLINE", str(online))
    s = cpu_cache_topology.status()
    # L1d=32K, L1i=32K, L2=4M expected
    assert s["l1d_kb"] == 32
    assert s["l2_kb"] == 4096
