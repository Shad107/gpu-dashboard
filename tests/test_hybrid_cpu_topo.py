"""Tests for modules/hybrid_cpu_topo.py — R&D #42.2."""
from __future__ import annotations

from pathlib import Path

import pytest

from gpu_dashboard.modules import hybrid_cpu_topo as mod


def _mk_cpu(root: Path, cpu: int, *, package: int = 0, die: int = 0,
              cluster: int | None = None, core: int | None = None,
              max_freq_khz: int | None = None):
    cdir = root / f"cpu{cpu}"
    topo = cdir / "topology"
    topo.mkdir(parents=True, exist_ok=True)
    (topo / "physical_package_id").write_text(str(package) + "\n")
    (topo / "die_id").write_text(str(die) + "\n")
    if cluster is not None:
        (topo / "cluster_id").write_text(str(cluster) + "\n")
    if core is not None:
        (topo / "core_id").write_text(str(core) + "\n")
    if max_freq_khz is not None:
        cpufreq = cdir / "cpufreq"
        cpufreq.mkdir(exist_ok=True)
        (cpufreq / "cpuinfo_max_freq").write_text(
            str(max_freq_khz) + "\n")


# --- list_cpus -----------------------------------------------------

def test_list_cpus_numeric_sort(tmp_path):
    for n in [0, 1, 10, 2]:
        _mk_cpu(tmp_path, n)
    assert mod.list_cpus(str(tmp_path)) == [0, 1, 2, 10]


def test_list_cpus_missing(tmp_path):
    assert mod.list_cpus(str(tmp_path / "nope")) == []


# --- read_cpu_topology --------------------------------------------

def test_read_cpu_topology_full(tmp_path):
    _mk_cpu(tmp_path, 0, package=0, die=0, cluster=0, core=0,
              max_freq_khz=5000000)
    r = mod.read_cpu_topology(str(tmp_path), 0)
    assert r["package_id"] == 0
    assert r["max_freq_khz"] == 5000000


def test_read_cpu_topology_missing_cpufreq(tmp_path):
    _mk_cpu(tmp_path, 0)
    r = mod.read_cpu_topology(str(tmp_path), 0)
    assert r["max_freq_khz"] is None


# --- freq_tiers ----------------------------------------------------

def test_freq_tiers_distinct():
    rows = [{"max_freq_khz": 5000000},
              {"max_freq_khz": 4000000},
              {"max_freq_khz": 5000000}]
    assert mod.freq_tiers(rows) == [5000000, 4000000]


def test_freq_tiers_skips_none_and_zero():
    rows = [{"max_freq_khz": None}, {"max_freq_khz": 0},
              {"max_freq_khz": 3000000}]
    assert mod.freq_tiers(rows) == [3000000]


# --- classify ------------------------------------------------------

def _row(cpu, package=0, die=0, cluster=0, core=None, freq=None):
    return {"cpu": cpu, "package_id": package, "die_id": die,
              "cluster_id": cluster,
              "core_id": cpu if core is None else core,
              "max_freq_khz": freq}


def test_classify_unknown_when_empty():
    v = mod.classify([])
    assert v["verdict"] == "unknown"


def test_classify_qemu_or_masked():
    # Each vCPU its own cluster, no cpufreq — qemu pattern.
    rows = [_row(i, cluster=i) for i in range(8)]
    v = mod.classify(rows)
    assert v["verdict"] == "qemu_or_masked"


def test_classify_p_e_hybrid():
    # 6 P-cores at 5.4 GHz, 8 E-cores at 4.0 GHz (Alder pattern).
    rows = [_row(i, cluster=0, freq=5400000) for i in range(6)]
    rows += [_row(6 + i, cluster=1, freq=4000000) for i in range(8)]
    v = mod.classify(rows)
    assert v["verdict"] == "p_e_hybrid"
    assert "P-cluster = 6" in v["reason"]
    assert "5400 MHz" in v["reason"]


def test_classify_multi_ccd():
    # 8 CPUs, all 4 GHz, package 0 with two die_ids.
    rows = [_row(i, die=0, freq=4000000) for i in range(4)]
    rows += [_row(4 + i, die=1, freq=4000000) for i in range(4)]
    v = mod.classify(rows)
    assert v["verdict"] == "multi_ccd_or_multi_die"
    assert "die 0" in v["reason"]


def test_classify_multi_cluster_uniform():
    # 8 CPUs same package + die, but 2 cluster_ids at same freq.
    rows = [_row(i, cluster=0, freq=4000000) for i in range(4)]
    rows += [_row(4 + i, cluster=1, freq=4000000) for i in range(4)]
    v = mod.classify(rows)
    assert v["verdict"] == "multi_cluster_uniform"


def test_classify_uniform_topology():
    # 4 CPUs same everything.
    rows = [_row(i, cluster=0, freq=4000000) for i in range(4)]
    v = mod.classify(rows)
    assert v["verdict"] == "uniform_topology"


def test_classify_p_e_wins_over_multi_die():
    # If a box has BOTH (very rare but possible) — P/E is the more
    # actionable advice, take it.
    rows = [_row(0, die=0, freq=5000000),
              _row(1, die=0, freq=3000000),
              _row(2, die=1, freq=5000000)]
    v = mod.classify(rows)
    assert v["verdict"] == "p_e_hybrid"


# --- status integration -------------------------------------------

def test_status_qemu_layout(monkeypatch, tmp_path):
    # 12 vCPUs, each its own cluster_id, no cpufreq.
    for i in range(12):
        _mk_cpu(tmp_path, i, package=0, die=0, cluster=i, core=i)
    monkeypatch.setattr(mod, "_SYS_CPU", str(tmp_path))
    out = mod.status()
    assert out["ok"] is True
    assert out["cpu_count"] == 12
    assert out["packages"] == [0]
    assert out["verdict"]["verdict"] == "qemu_or_masked"


def test_status_alder_lake_layout(monkeypatch, tmp_path):
    # 6 P-cores @ 5.4, 8 E-cores @ 4.0
    for i in range(6):
        _mk_cpu(tmp_path, i, package=0, die=0, cluster=0,
                  core=i, max_freq_khz=5400000)
    for i in range(8):
        _mk_cpu(tmp_path, 6 + i, package=0, die=0, cluster=1,
                  core=6 + i, max_freq_khz=4000000)
    monkeypatch.setattr(mod, "_SYS_CPU", str(tmp_path))
    out = mod.status()
    assert out["verdict"]["verdict"] == "p_e_hybrid"
    assert out["freq_tiers_khz"] == [5400000, 4000000]


def test_status_unknown(monkeypatch, tmp_path):
    monkeypatch.setattr(mod, "_SYS_CPU", str(tmp_path / "nope"))
    out = mod.status()
    assert out["ok"] is False
    assert out["verdict"]["verdict"] == "unknown"
