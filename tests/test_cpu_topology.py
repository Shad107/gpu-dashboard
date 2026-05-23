"""Tests for modules/cpu_topology.py — R&D #31.3 CPU topology + governor."""
from __future__ import annotations

from pathlib import Path

import pytest

from gpu_dashboard.modules import cpu_topology


def _mk_cpu(root: Path, n: int, *, core_id: str = "0", pkg: str = "0",
              thread_sibs: str = "", cluster: str = "0",
              governor: str | None = None,
              cur_freq_khz: str | None = None,
              max_freq_khz: str | None = None,
              cpuinfo_max_khz: str | None = None):
    base = root / f"cpu{n}"
    topo = base / "topology"
    topo.mkdir(parents=True)
    (topo / "core_id").write_text(core_id + "\n")
    (topo / "physical_package_id").write_text(pkg + "\n")
    (topo / "thread_siblings_list").write_text((thread_sibs or str(n)) + "\n")
    (topo / "cluster_id").write_text(cluster + "\n")
    if governor or cur_freq_khz or max_freq_khz or cpuinfo_max_khz:
        cf = base / "cpufreq"
        cf.mkdir()
        if governor:
            (cf / "scaling_governor").write_text(governor + "\n")
        if cur_freq_khz:
            (cf / "scaling_cur_freq").write_text(cur_freq_khz + "\n")
        if max_freq_khz:
            (cf / "scaling_max_freq").write_text(max_freq_khz + "\n")
        if cpuinfo_max_khz:
            (cf / "cpuinfo_max_freq").write_text(cpuinfo_max_khz + "\n")


def _mk_online(root: Path, online_range: str = "0-11"):
    (root / "online").write_text(online_range + "\n")
    (root / "possible").write_text(online_range + "\n")


def _mk_hybrid(root: Path, p_cores: str, e_cores: str):
    types = root / "types"
    (types / "intel_core").mkdir(parents=True)
    (types / "intel_core" / "cpus").write_text(p_cores + "\n")
    (types / "intel_atom").mkdir(parents=True)
    (types / "intel_atom" / "cpus").write_text(e_cores + "\n")


# --- helpers --------------------------------------------------------

def test_parse_cpu_list_single():
    assert cpu_topology.parse_cpu_list("3") == [3]


def test_parse_cpu_list_range():
    assert cpu_topology.parse_cpu_list("0-3") == [0, 1, 2, 3]


def test_parse_cpu_list_mixed():
    assert cpu_topology.parse_cpu_list("0-3,5,8-9") == [0, 1, 2, 3, 5, 8, 9]


def test_parse_cpu_list_empty():
    assert cpu_topology.parse_cpu_list("") == []
    assert cpu_topology.parse_cpu_list(None) == []


def test_list_online_cpus(tmp_path):
    _mk_online(tmp_path, "0-3")
    assert cpu_topology.list_online_cpus(str(tmp_path)) == [0, 1, 2, 3]


def test_list_online_cpus_missing_falls_back_to_dir_scan(tmp_path):
    # No online file → enumerate cpu*/topology
    _mk_cpu(tmp_path, 0)
    _mk_cpu(tmp_path, 1)
    assert cpu_topology.list_online_cpus(str(tmp_path)) == [0, 1]


def test_read_topology_returns_dict(tmp_path):
    _mk_cpu(tmp_path, 0, core_id="0", pkg="0", thread_sibs="0,12")
    t = cpu_topology.read_topology(str(tmp_path), 0)
    assert t["core_id"] == 0
    assert t["package_id"] == 0
    assert t["thread_siblings"] == [0, 12]


def test_read_governor(tmp_path):
    _mk_cpu(tmp_path, 0, governor="performance")
    assert cpu_topology.read_governor(str(tmp_path), 0) == "performance"


def test_read_governor_none_when_cpufreq_absent(tmp_path):
    _mk_cpu(tmp_path, 0)
    assert cpu_topology.read_governor(str(tmp_path), 0) is None


def test_read_max_freq_khz(tmp_path):
    _mk_cpu(tmp_path, 0, cpuinfo_max_khz="5400000")
    assert cpu_topology.read_max_freq_khz(str(tmp_path), 0) == 5400000


# --- hybrid detection ----------------------------------------------

def test_detect_hybrid_none_when_no_types(tmp_path):
    assert cpu_topology.detect_hybrid(str(tmp_path)) is None


def test_detect_hybrid_alder_lake_style(tmp_path):
    _mk_hybrid(tmp_path, p_cores="0-15", e_cores="16-23")
    h = cpu_topology.detect_hybrid(str(tmp_path))
    assert h is not None
    assert h["p_cores"] == list(range(16))
    assert h["e_cores"] == list(range(16, 24))


# --- classify ------------------------------------------------------

def test_classify_balanced_performance():
    v = cpu_topology.classify(
        cpus=[{"id": 0, "governor": "performance"},
              {"id": 1, "governor": "performance"}],
        hybrid=None,
    )
    assert v["verdict"] == "balanced"
    assert v["recommendation"] == ""


def test_classify_powersave_is_bad_for_inference():
    v = cpu_topology.classify(
        cpus=[{"id": 0, "governor": "powersave"},
              {"id": 1, "governor": "powersave"}],
        hybrid=None,
    )
    assert v["verdict"] == "powersave"
    assert "performance" in v["recommendation"]
    assert "cpupower" in v["recommendation"] or "cpufreq" in v["recommendation"]


def test_classify_missing_cpufreq_when_all_none():
    v = cpu_topology.classify(
        cpus=[{"id": 0, "governor": None}, {"id": 1, "governor": None}],
        hybrid=None,
    )
    assert v["verdict"] == "missing_cpufreq"
    assert "VM" in v["reason"] or "cpufreq" in v["reason"].lower()


def test_classify_hybrid_unaware():
    v = cpu_topology.classify(
        cpus=[{"id": i, "governor": "performance"} for i in range(24)],
        hybrid={"p_cores": list(range(16)),
                  "e_cores": list(range(16, 24))},
    )
    assert v["verdict"] == "hybrid_unaware"
    assert "taskset" in v["recommendation"] or "CPUAffinity" in v["recommendation"]
    assert "0-15" in v["recommendation"]  # P-core list in recipe


def test_classify_mixed_governors_picks_worst():
    # Most CPUs performance, one powersave → still flagged
    cpus = [{"id": i, "governor": "performance"} for i in range(11)]
    cpus.append({"id": 11, "governor": "powersave"})
    v = cpu_topology.classify(cpus=cpus, hybrid=None)
    assert v["verdict"] == "powersave"


def test_classify_schedutil_is_balanced():
    v = cpu_topology.classify(
        cpus=[{"id": 0, "governor": "schedutil"}],
        hybrid=None,
    )
    assert v["verdict"] == "balanced"


# --- status --------------------------------------------------------

def test_status_vm_no_cpufreq(tmp_path, monkeypatch):
    # The live-rig case: 12 vCPUs, no cpufreq dir
    for i in range(12):
        _mk_cpu(tmp_path, i, core_id=str(i))
    _mk_online(tmp_path, "0-11")
    monkeypatch.setattr(cpu_topology, "_CPU_ROOT", str(tmp_path))
    s = cpu_topology.status()
    assert s["ok"] is True
    assert s["cpu_count"] == 12
    assert s["verdict"]["verdict"] == "missing_cpufreq"
    assert s["hybrid"] is None
    assert s["smt_enabled"] is False


def test_status_with_smt_enabled(tmp_path, monkeypatch):
    _mk_cpu(tmp_path, 0, core_id="0", thread_sibs="0,8")
    _mk_cpu(tmp_path, 8, core_id="0", thread_sibs="0,8")
    _mk_online(tmp_path, "0,8")
    monkeypatch.setattr(cpu_topology, "_CPU_ROOT", str(tmp_path))
    s = cpu_topology.status()
    assert s["smt_enabled"] is True


def test_status_with_hybrid_cpu(tmp_path, monkeypatch):
    # Alder Lake-like: 6 P-cores (with HT → 12 threads) + 4 E-cores
    for i in range(16):
        _mk_cpu(tmp_path, i, core_id=str(i % 6 if i < 12 else 12 + (i - 12)),
                governor="performance")
    _mk_online(tmp_path, "0-15")
    _mk_hybrid(tmp_path, p_cores="0-11", e_cores="12-15")
    monkeypatch.setattr(cpu_topology, "_CPU_ROOT", str(tmp_path))
    s = cpu_topology.status()
    assert s["hybrid"] is not None
    assert s["hybrid"]["p_cores"] == list(range(12))
    assert s["hybrid"]["e_cores"] == [12, 13, 14, 15]
    assert s["verdict"]["verdict"] == "hybrid_unaware"


def test_status_performance_governor_balanced(tmp_path, monkeypatch):
    for i in range(4):
        _mk_cpu(tmp_path, i, core_id=str(i), governor="performance")
    _mk_online(tmp_path, "0-3")
    monkeypatch.setattr(cpu_topology, "_CPU_ROOT", str(tmp_path))
    s = cpu_topology.status()
    assert s["verdict"]["verdict"] == "balanced"


def test_status_max_freq_aggregated(tmp_path, monkeypatch):
    _mk_cpu(tmp_path, 0, governor="performance",
            cpuinfo_max_khz="5400000")
    _mk_cpu(tmp_path, 1, governor="performance",
            cpuinfo_max_khz="5400000")
    _mk_online(tmp_path, "0-1")
    monkeypatch.setattr(cpu_topology, "_CPU_ROOT", str(tmp_path))
    s = cpu_topology.status()
    assert s["max_freq_mhz"] == 5400
