"""Tests for modules/cpu_smt_control_audit.py — R&D #87.3."""
from __future__ import annotations

import pytest

from gpu_dashboard.modules import cpu_smt_control_audit as mod


def _mk_smt(tmp_path, *, control="on", active="1"):
    d = tmp_path / "smt"
    d.mkdir(parents=True, exist_ok=True)
    (d / "control").write_text(control + "\n")
    (d / "active").write_text(active + "\n")
    return str(d)


def _mk_vulns(tmp_path, vulns):
    d = tmp_path / "vulnerabilities"
    d.mkdir(parents=True, exist_ok=True)
    for name, text in vulns.items():
        (d / name).write_text(text + "\n")
    return str(d)


# --- _has_smt_vulnerable_text ----------------------------------

def test_smt_vulnerable_text_yes():
    assert mod._has_smt_vulnerable_text(
        "Mitigation: PTI; SMT vulnerable") is True


def test_smt_vulnerable_text_no():
    assert mod._has_smt_vulnerable_text(
        "Mitigation: PTI") is False


def test_smt_vulnerable_text_host_unknown():
    assert mod._has_smt_vulnerable_text(
        "Mitigation: PTI; SMT Host state unknown") is True


def test_smt_vulnerable_text_empty():
    assert mod._has_smt_vulnerable_text("") is False


# --- read_smt_state --------------------------------------------

def test_read_smt_missing(tmp_path):
    out = mod.read_smt_state(str(tmp_path / "nope"))
    assert out["control"] == ""


def test_read_smt(tmp_path):
    r = _mk_smt(tmp_path, control="off", active="0")
    out = mod.read_smt_state(r)
    assert out["control"] == "off"
    assert out["active"] == "0"


# --- read_vulns ------------------------------------------------

def test_read_vulns_missing(tmp_path):
    assert mod.read_vulns(str(tmp_path / "nope")) == {}


def test_read_vulns_populated(tmp_path):
    r = _mk_vulns(tmp_path, {
        "l1tf": "Mitigation: PTE Inversion",
        "mds": "Not affected",
        "irrelevant": "should not appear",
    })
    out = mod.read_vulns(r)
    assert "l1tf" in out
    assert "mds" in out
    assert "irrelevant" not in out


# --- classify --------------------------------------------------

def test_classify_unknown_no_control():
    v = mod.classify({"control": ""}, {}, False)
    assert v["verdict"] == "unknown"


def test_classify_unknown_notsupported():
    v = mod.classify({"control": "notsupported"},
                          {}, True)
    assert v["verdict"] == "unknown"


def test_classify_requires_root():
    v = mod.classify({"control": "on"}, {}, False)
    assert v["verdict"] == "requires_root"


def test_classify_ok_smt_on_with_mitigations():
    v = mod.classify(
        {"control": "on"},
        {"mds": "Mitigation: Clear CPU buffers"},
        True)
    assert v["verdict"] == "ok"


def test_classify_smt_off_still_vulnerable():
    v = mod.classify(
        {"control": "forceoff"},
        {"mds": "Mitigation: Clear CPU buffers; SMT vulnerable"},
        True)
    assert v["verdict"] == "smt_off_still_vulnerable"


def test_classify_smt_off_over_mitigated():
    # SMT off + all vulns "Not affected" = pure perf tax
    v = mod.classify(
        {"control": "off"},
        {"l1tf": "Not affected",
         "mds": "Not affected",
         "taa": "Not affected"},
        True)
    assert v["verdict"] == "smt_off_over_mitigated"


def test_classify_smt_off_vulnerable_not_smt_related():
    # SMT off and some vuln remains (Vulnerable:) but not
    # SMT-related → still "smt_off" since vulns exist but
    # the over_mitigated check sees "Vulnerable" → ok path
    v = mod.classify(
        {"control": "off"},
        {"mds": "Vulnerable; No microcode"},
        True)
    # any_vulnerable is True → falls through to ok
    assert v["verdict"] == "ok"


# Priority : still_vulnerable > over_mitigated
def test_priority_still_vulnerable_over_over_mitigated():
    # Conflicting state: SMT off AND one vuln says SMT
    # vulnerable AND others "Not affected"
    v = mod.classify(
        {"control": "off"},
        {"l1tf": "Mitigation: PTE Inversion; SMT vulnerable",
         "mds": "Not affected"},
        True)
    assert v["verdict"] == "smt_off_still_vulnerable"


# --- status integration ----------------------------------------

def test_status_unknown(tmp_path):
    out = mod.status(None,
                       str(tmp_path / "nope_smt"),
                       str(tmp_path / "nope_vuln"))
    assert out["verdict"]["verdict"] == "unknown"


def test_status_ok_synthetic(tmp_path):
    smt = _mk_smt(tmp_path, control="on", active="1")
    vulns = _mk_vulns(tmp_path, {
        "mds": "Mitigation: Clear CPU buffers"})
    out = mod.status(None, smt, vulns)
    assert out["smt_control"] == "on"
    assert out["verdict"]["verdict"] == "ok"


def test_status_off_still_vulnerable_synthetic(tmp_path):
    smt = _mk_smt(tmp_path, control="forceoff", active="0")
    vulns = _mk_vulns(tmp_path, {
        "mds": "Mitigation: Clear CPU buffers; SMT vulnerable"})
    out = mod.status(None, smt, vulns)
    assert out["ok"] is False
    assert (out["verdict"]["verdict"]
            == "smt_off_still_vulnerable")


def test_status_over_mitigated_synthetic(tmp_path):
    smt = _mk_smt(tmp_path, control="off", active="0")
    vulns = _mk_vulns(tmp_path, {
        "l1tf": "Not affected",
        "mds": "Not affected"})
    out = mod.status(None, smt, vulns)
    assert (out["verdict"]["verdict"]
            == "smt_off_over_mitigated")
