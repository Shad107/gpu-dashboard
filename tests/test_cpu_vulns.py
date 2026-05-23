"""Tests for modules/cpu_vulns.py — R&D #37.1 CPU vulnerabilities audit."""
from __future__ import annotations

from pathlib import Path

import pytest

from gpu_dashboard.modules import cpu_vulns


def _mk_vulns(root: Path, **named):
    """Each kwarg name = vulnerability file name, value = its content."""
    root.mkdir(parents=True, exist_ok=True)
    for k, v in named.items():
        (root / k).write_text(v + "\n")


# --- parse_state ----------------------------------------------

def test_parse_state_not_affected():
    s = cpu_vulns.parse_state("Not affected")
    assert s["state"] == "not_affected"


def test_parse_state_mitigation():
    s = cpu_vulns.parse_state("Mitigation: Speculative Store Bypass disabled via prctl")
    assert s["state"] == "mitigated"
    assert "speculative store bypass" in s["detail"].lower()


def test_parse_state_vulnerable_with_reason():
    s = cpu_vulns.parse_state("Vulnerable: No microcode")
    assert s["state"] == "vulnerable"
    assert "no microcode" in s["detail"].lower()


def test_parse_state_vulnerable_bare():
    s = cpu_vulns.parse_state("Vulnerable")
    assert s["state"] == "vulnerable"


def test_parse_state_empty():
    s = cpu_vulns.parse_state("")
    assert s["state"] == "unknown"


def test_parse_state_unknown_text():
    s = cpu_vulns.parse_state("Some kernel oddness")
    assert s["state"] == "unknown"


# --- list_vulns ----------------------------------------------

def test_list_vulns_returns_sorted(tmp_path):
    _mk_vulns(tmp_path,
              spectre_v2="Mitigation: foo",
              spectre_v1="Mitigation: bar",
              meltdown="Not affected")
    vs = cpu_vulns.list_vulns(str(tmp_path))
    assert vs == ["meltdown", "spectre_v1", "spectre_v2"]


def test_list_vulns_empty_when_dir_missing(tmp_path):
    assert cpu_vulns.list_vulns(str(tmp_path / "absent")) == []


# --- classify -----------------------------------------------

def test_classify_clean_all_not_affected():
    rows = [
        {"name": "meltdown", "state": "not_affected"},
        {"name": "spectre_v1", "state": "not_affected"},
    ]
    v = cpu_vulns.classify(rows)
    assert v["verdict"] == "clean"


def test_classify_mitigated_when_no_vulnerable():
    rows = [
        {"name": "spectre_v1", "state": "mitigated", "detail": "usercopy"},
        {"name": "spectre_v2", "state": "mitigated", "detail": "Enhanced IBRS"},
        {"name": "meltdown", "state": "not_affected"},
    ]
    v = cpu_vulns.classify(rows)
    assert v["verdict"] == "mitigated"
    assert "spectre" in v["reason"].lower() or "2" in v["reason"]


def test_classify_vulnerable_when_any_vulnerable():
    rows = [
        {"name": "reg_file_data_sampling", "state": "vulnerable",
         "detail": "No microcode"},
        {"name": "spectre_v2", "state": "mitigated"},
    ]
    v = cpu_vulns.classify(rows)
    assert v["verdict"] == "vulnerable"
    assert "reg_file_data_sampling" in v["reason"]


def test_classify_unknown_when_empty():
    v = cpu_vulns.classify([])
    assert v["verdict"] == "unknown"


def test_classify_recipe_includes_mitigations_off():
    rows = [
        {"name": "spectre_v1", "state": "mitigated"},
        {"name": "spectre_v2", "state": "mitigated"},
    ]
    v = cpu_vulns.classify(rows)
    assert "mitigations=" in v["recommendation"]


def test_classify_vulnerable_recipe_microcode_first():
    rows = [
        {"name": "reg_file_data_sampling", "state": "vulnerable",
         "detail": "No microcode"},
    ]
    v = cpu_vulns.classify(rows)
    assert "microcode" in v["recommendation"].lower()


# --- status ---------------------------------------------

def test_status_live_mixed_state(tmp_path, monkeypatch):
    # The live-rig state: mostly Not affected, a few mitigated,
    # one vulnerable.
    _mk_vulns(tmp_path,
              meltdown="Not affected",
              spectre_v1="Mitigation: usercopy/swapgs barriers and __user pointer sanitization",
              spectre_v2="Mitigation: Enhanced / Automatic IBRS; IBPB: conditional",
              spec_store_bypass="Mitigation: Speculative Store Bypass disabled via prctl",
              reg_file_data_sampling="Vulnerable: No microcode",
              mds="Not affected",
              srbds="Not affected",
              retbleed="Not affected")
    monkeypatch.setattr(cpu_vulns, "_VULN_ROOT", str(tmp_path))
    s = cpu_vulns.status()
    assert s["ok"] is True
    assert s["vulnerability_count"] == 8
    assert s["counts"]["vulnerable"] == 1
    assert s["counts"]["mitigated"] == 3
    assert s["counts"]["not_affected"] == 4
    assert s["verdict"]["verdict"] == "vulnerable"


def test_status_clean_kernel(tmp_path, monkeypatch):
    _mk_vulns(tmp_path,
              meltdown="Not affected",
              spectre_v1="Not affected",
              spectre_v2="Not affected")
    monkeypatch.setattr(cpu_vulns, "_VULN_ROOT", str(tmp_path))
    s = cpu_vulns.status()
    assert s["verdict"]["verdict"] == "clean"


def test_status_mitigated_kernel(tmp_path, monkeypatch):
    _mk_vulns(tmp_path,
              spectre_v1="Mitigation: usercopy",
              spectre_v2="Mitigation: IBRS",
              meltdown="Not affected")
    monkeypatch.setattr(cpu_vulns, "_VULN_ROOT", str(tmp_path))
    s = cpu_vulns.status()
    assert s["verdict"]["verdict"] == "mitigated"


def test_status_no_vulns_dir(tmp_path, monkeypatch):
    monkeypatch.setattr(cpu_vulns, "_VULN_ROOT",
                          str(tmp_path / "absent"))
    s = cpu_vulns.status()
    assert s["ok"] is False
    assert s["error"] == "vulns_unavailable"


def test_status_exposes_per_vuln_rows(tmp_path, monkeypatch):
    _mk_vulns(tmp_path,
              meltdown="Not affected",
              spectre_v2="Mitigation: IBRS")
    monkeypatch.setattr(cpu_vulns, "_VULN_ROOT", str(tmp_path))
    s = cpu_vulns.status()
    names = [r["name"] for r in s["rows"]]
    assert "meltdown" in names
    assert "spectre_v2" in names
