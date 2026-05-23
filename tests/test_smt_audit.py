"""Tests for modules/smt_audit.py — R&D #35.4 SMT + offline-core audit."""
from __future__ import annotations

from pathlib import Path

import pytest

from gpu_dashboard.modules import smt_audit


def _mk_cpu_topology(root: Path, *, possible: str = "0-11",
                       online: str = "0-11", offline: str = "",
                       smt_control: str | None = "on",
                       smt_active: str | None = "1",
                       offline_cpus: list | None = None):
    root.mkdir(parents=True, exist_ok=True)
    (root / "possible").write_text(possible + "\n")
    (root / "online").write_text(online + "\n")
    (root / "offline").write_text(offline + "\n")
    if smt_control is not None or smt_active is not None:
        smt_dir = root / "smt"
        smt_dir.mkdir(exist_ok=True)
        if smt_control is not None:
            (smt_dir / "control").write_text(smt_control + "\n")
        if smt_active is not None:
            (smt_dir / "active").write_text(smt_active + "\n")
    # Per-CPU online files (cpu0 has no online file — boot CPU)
    for n in range(12):
        d = root / f"cpu{n}"
        d.mkdir(exist_ok=True)
        if n != 0:
            on = "0" if (offline_cpus and n in offline_cpus) else "1"
            (d / "online").write_text(on + "\n")


# --- parse_cpu_list ----------------------------------------------

def test_parse_cpu_list_range():
    assert smt_audit.parse_cpu_list("0-11") == list(range(12))


def test_parse_cpu_list_single():
    assert smt_audit.parse_cpu_list("3") == [3]


def test_parse_cpu_list_mixed():
    assert smt_audit.parse_cpu_list("0,2-3,5") == [0, 2, 3, 5]


def test_parse_cpu_list_empty():
    assert smt_audit.parse_cpu_list("") == []
    assert smt_audit.parse_cpu_list(None) == []


# --- read helpers -----------------------------------------------

def test_read_smt_control(tmp_path):
    _mk_cpu_topology(tmp_path, smt_control="on")
    assert smt_audit.read_smt_control(str(tmp_path)) == "on"


def test_read_smt_active(tmp_path):
    _mk_cpu_topology(tmp_path, smt_active="1")
    assert smt_audit.read_smt_active(str(tmp_path)) == 1


def test_read_smt_missing_returns_none(tmp_path):
    _mk_cpu_topology(tmp_path, smt_control=None, smt_active=None)
    assert smt_audit.read_smt_control(str(tmp_path)) is None


# --- offline-core detection -------------------------------------

def test_find_offline_cores_none(tmp_path):
    _mk_cpu_topology(tmp_path, offline="")
    cores = smt_audit.find_offline_cores(str(tmp_path))
    assert cores == []


def test_find_offline_cores_from_offline_file(tmp_path):
    _mk_cpu_topology(tmp_path, offline="3,7", offline_cpus=[3, 7])
    cores = smt_audit.find_offline_cores(str(tmp_path))
    assert sorted(cores) == [3, 7]


def test_find_offline_cores_per_cpu_online(tmp_path):
    # offline file empty but cpu5/online=0 → detect via per-cpu fallback
    _mk_cpu_topology(tmp_path, offline="", offline_cpus=[5])
    cores = smt_audit.find_offline_cores(str(tmp_path))
    assert 5 in cores


# --- classify ---------------------------------------------------

def test_classify_smt_on():
    v = smt_audit.classify(smt_control="on", smt_active=1,
                              possible_count=12, online_count=12,
                              offline_cores=[])
    assert v["verdict"] == "smt_on"


def test_classify_smt_off_explicit():
    v = smt_audit.classify(smt_control="off", smt_active=0,
                              possible_count=12, online_count=6,
                              offline_cores=[])
    assert v["verdict"] == "smt_off"


def test_classify_smt_forceoff():
    # smt_control=forceoff (mitigations) → still smt_off verdict
    v = smt_audit.classify(smt_control="forceoff", smt_active=0,
                              possible_count=12, online_count=6,
                              offline_cores=[])
    assert v["verdict"] == "smt_off"


def test_classify_smt_not_supported_vm():
    # The live-rig case: VM with notsupported control
    v = smt_audit.classify(smt_control="notsupported", smt_active=0,
                              possible_count=12, online_count=12,
                              offline_cores=[])
    assert v["verdict"] == "smt_not_supported"


def test_classify_offline_cores_outranks_smt_on():
    v = smt_audit.classify(smt_control="on", smt_active=1,
                              possible_count=12, online_count=10,
                              offline_cores=[3, 7])
    assert v["verdict"] == "cores_offline"
    assert "3" in v["reason"] or "7" in v["reason"]


def test_classify_offline_cores_recipe_uses_echo():
    v = smt_audit.classify(smt_control="on", smt_active=1,
                              possible_count=12, online_count=10,
                              offline_cores=[3, 7])
    rec = v["recommendation"]
    assert "echo 1" in rec
    assert "/sys/devices/system/cpu/cpu3/online" in rec or \
           "/sys/devices/system/cpu/cpu7/online" in rec


def test_classify_unknown_when_no_smt_info():
    v = smt_audit.classify(smt_control=None, smt_active=None,
                              possible_count=12, online_count=12,
                              offline_cores=[])
    assert v["verdict"] == "unknown"


# --- status -----------------------------------------------------

def test_status_vm_no_smt(tmp_path, monkeypatch):
    # The live-rig case
    _mk_cpu_topology(tmp_path, smt_control="notsupported",
                       smt_active="0", possible="0-11", online="0-11",
                       offline="")
    monkeypatch.setattr(smt_audit, "_CPU_ROOT", str(tmp_path))
    s = smt_audit.status()
    assert s["ok"] is True
    assert s["smt_control"] == "notsupported"
    assert s["smt_active"] == 0
    assert s["possible_count"] == 12
    assert s["online_count"] == 12
    assert s["verdict"]["verdict"] == "smt_not_supported"


def test_status_bare_metal_smt_on(tmp_path, monkeypatch):
    _mk_cpu_topology(tmp_path, smt_control="on", smt_active="1",
                       possible="0-15", online="0-15", offline="")
    monkeypatch.setattr(smt_audit, "_CPU_ROOT", str(tmp_path))
    s = smt_audit.status()
    assert s["verdict"]["verdict"] == "smt_on"


def test_status_offline_cores(tmp_path, monkeypatch):
    _mk_cpu_topology(tmp_path, smt_control="on", smt_active="1",
                       possible="0-11", online="0-2,4-11", offline="3",
                       offline_cpus=[3])
    monkeypatch.setattr(smt_audit, "_CPU_ROOT", str(tmp_path))
    s = smt_audit.status()
    assert s["verdict"]["verdict"] == "cores_offline"
    assert s["offline_cores"] == [3]


def test_status_explicit_smt_off(tmp_path, monkeypatch):
    _mk_cpu_topology(tmp_path, smt_control="off", smt_active="0",
                       possible="0-11", online="0-5",
                       offline="6-11", offline_cpus=[6, 7, 8, 9, 10, 11])
    monkeypatch.setattr(smt_audit, "_CPU_ROOT", str(tmp_path))
    s = smt_audit.status()
    # When SMT is explicitly off, the offlined-hyperthread cores
    # don't count as "wasted hardware" — that IS the configuration.
    # smt_off should win over cores_offline.
    assert s["verdict"]["verdict"] == "smt_off"
