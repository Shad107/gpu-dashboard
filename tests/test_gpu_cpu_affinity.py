"""Tests for modules/gpu_cpu_affinity.py — R&D #37.2 GPU↔CPU PCIe affinity."""
from __future__ import annotations

from pathlib import Path

import pytest

from gpu_dashboard.modules import gpu_cpu_affinity


def _mk_pci_dev(root: Path, bdf: str, *, vendor: str = "0x10de",
                   klass: str = "0x030000",
                   local_cpulist: str = "0-11",
                   local_cpus: str = "fff",
                   numa_node: str = "-1"):
    d = root / bdf
    d.mkdir(parents=True)
    (d / "vendor").write_text(vendor + "\n")
    (d / "class").write_text(klass + "\n")
    (d / "local_cpulist").write_text(local_cpulist + "\n")
    (d / "local_cpus").write_text(local_cpus + "\n")
    (d / "numa_node").write_text(numa_node + "\n")


# --- find_nvidia_bdfs ---------------------------------------------

def test_find_nvidia_bdfs_vga_only(tmp_path):
    _mk_pci_dev(tmp_path, "0000:01:00.0", klass="0x030000")  # GPU
    _mk_pci_dev(tmp_path, "0000:01:00.1", klass="0x040300")  # HDA audio
    _mk_pci_dev(tmp_path, "0000:00:1f.3", vendor="0x8086", klass="0x040300")
    gpus = gpu_cpu_affinity.find_nvidia_bdfs(str(tmp_path))
    assert gpus == ["0000:01:00.0"]


def test_find_nvidia_bdfs_empty(tmp_path):
    assert gpu_cpu_affinity.find_nvidia_bdfs(str(tmp_path)) == []


# --- read_pci_field ----------------------------------------------

def test_read_local_cpulist(tmp_path):
    _mk_pci_dev(tmp_path, "0000:01:00.0", local_cpulist="0-7")
    assert gpu_cpu_affinity.read_local_cpulist(str(tmp_path),
                                                  "0000:01:00.0") == "0-7"


def test_read_numa_node(tmp_path):
    _mk_pci_dev(tmp_path, "0000:01:00.0", numa_node="1")
    assert gpu_cpu_affinity.read_numa_node(str(tmp_path),
                                              "0000:01:00.0") == 1


def test_read_numa_node_minus_one(tmp_path):
    _mk_pci_dev(tmp_path, "0000:01:00.0", numa_node="-1")
    assert gpu_cpu_affinity.read_numa_node(str(tmp_path),
                                              "0000:01:00.0") == -1


def test_read_missing_returns_none(tmp_path):
    (tmp_path / "0000:01:00.0").mkdir()
    assert gpu_cpu_affinity.read_local_cpulist(str(tmp_path),
                                                  "0000:01:00.0") is None


# --- parse_cpu_list ----------------------------------------------

def test_parse_cpu_list_range():
    assert gpu_cpu_affinity.parse_cpu_list("0-7") == list(range(8))


def test_parse_cpu_list_mixed():
    assert gpu_cpu_affinity.parse_cpu_list("0-3,8-11") == [0, 1, 2, 3, 8, 9, 10, 11]


def test_parse_cpu_list_empty():
    assert gpu_cpu_affinity.parse_cpu_list("") == []


# --- classify ----------------------------------------------------

def test_classify_no_gpus():
    v = gpu_cpu_affinity.classify(cards=[], total_cpus=12)
    assert v["verdict"] == "no_gpus"


def test_classify_single_node_when_local_equals_all():
    cards = [{"gpu_bdf": "0000:01:00.0", "local_cpulist": "0-11",
                "local_cpus_count": 12, "numa_node": -1}]
    v = gpu_cpu_affinity.classify(cards, total_cpus=12)
    assert v["verdict"] == "single_node_affinity"


def test_classify_constrained_affinity_on_multi_socket():
    # GPU is on CPU 0-7 ; full system has 16 CPUs → preference matters
    cards = [{"gpu_bdf": "0000:01:00.0", "local_cpulist": "0-7",
                "local_cpus_count": 8, "numa_node": 0}]
    v = gpu_cpu_affinity.classify(cards, total_cpus=16)
    assert v["verdict"] == "constrained_affinity"
    assert "0-7" in v["recommendation"]


def test_classify_unset_when_no_data():
    cards = [{"gpu_bdf": "0000:01:00.0", "local_cpulist": None,
                "local_cpus_count": 0, "numa_node": None}]
    v = gpu_cpu_affinity.classify(cards, total_cpus=12)
    assert v["verdict"] == "unset"


def test_classify_picks_worst_constrained_across_gpus():
    cards = [
        {"gpu_bdf": "0000:01:00.0", "local_cpulist": "0-15",
         "local_cpus_count": 16, "numa_node": 0},
        {"gpu_bdf": "0000:81:00.0", "local_cpulist": "16-31",
         "local_cpus_count": 16, "numa_node": 1},
    ]
    v = gpu_cpu_affinity.classify(cards, total_cpus=32)
    # Both GPUs are constrained (each only 50 % of CPUs is local)
    assert v["verdict"] == "constrained_affinity"


def test_classify_recipe_uses_local_cpulist():
    cards = [{"gpu_bdf": "0000:01:00.0", "local_cpulist": "0-7",
                "local_cpus_count": 8, "numa_node": 0}]
    v = gpu_cpu_affinity.classify(cards, total_cpus=16)
    rec = v["recommendation"]
    assert "0-7" in rec
    assert "CPUAffinity" in rec or "taskset" in rec or "numactl" in rec


# --- status -----------------------------------------------------

def test_status_no_gpus(tmp_path, monkeypatch):
    monkeypatch.setattr(gpu_cpu_affinity, "_PCI_ROOT", str(tmp_path))
    monkeypatch.setattr(gpu_cpu_affinity, "_CPU_ONLINE",
                          str(tmp_path / "online_missing"))
    s = gpu_cpu_affinity.status()
    assert s["ok"] is True
    assert s["gpu_count"] == 0
    assert s["verdict"]["verdict"] == "no_gpus"


def test_status_live_single_node(tmp_path, monkeypatch):
    # The live-rig case
    _mk_pci_dev(tmp_path, "0000:01:00.0", local_cpulist="0-11",
                  local_cpus="fff", numa_node="-1")
    online = tmp_path / "online"
    online.write_text("0-11\n")
    monkeypatch.setattr(gpu_cpu_affinity, "_PCI_ROOT", str(tmp_path))
    monkeypatch.setattr(gpu_cpu_affinity, "_CPU_ONLINE", str(online))
    s = gpu_cpu_affinity.status()
    assert s["gpu_count"] == 1
    assert s["total_cpus"] == 12
    card = s["cards"][0]
    assert card["local_cpulist"] == "0-11"
    assert card["numa_node"] == -1
    assert s["verdict"]["verdict"] == "single_node_affinity"


def test_status_constrained_multi_socket(tmp_path, monkeypatch):
    # GPU only sees half the CPUs as local
    _mk_pci_dev(tmp_path, "0000:01:00.0", local_cpulist="0-15",
                  local_cpus="ffff", numa_node="0")
    online = tmp_path / "online"
    online.write_text("0-31\n")
    monkeypatch.setattr(gpu_cpu_affinity, "_PCI_ROOT", str(tmp_path))
    monkeypatch.setattr(gpu_cpu_affinity, "_CPU_ONLINE", str(online))
    s = gpu_cpu_affinity.status()
    assert s["total_cpus"] == 32
    assert s["verdict"]["verdict"] == "constrained_affinity"


def test_status_exposes_total_cpus(tmp_path, monkeypatch):
    online = tmp_path / "online"
    online.write_text("0-15\n")
    monkeypatch.setattr(gpu_cpu_affinity, "_PCI_ROOT", str(tmp_path))
    monkeypatch.setattr(gpu_cpu_affinity, "_CPU_ONLINE", str(online))
    s = gpu_cpu_affinity.status()
    assert s["total_cpus"] == 16
