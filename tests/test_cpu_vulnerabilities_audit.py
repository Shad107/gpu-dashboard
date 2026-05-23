"""Tests for modules/cpu_vulnerabilities_audit.py — R&D #53.2."""
from __future__ import annotations

import pytest

from gpu_dashboard.modules import cpu_vulnerabilities_audit as mod


def _mk_vulns(root, mapping):
    root.mkdir(parents=True, exist_ok=True)
    for k, v in mapping.items():
        (root / k).write_text(v + "\n")


def _mk_smt(root, *, active=None, control=None):
    root.mkdir(parents=True, exist_ok=True)
    if active is not None:
        (root / "active").write_text(active + "\n")
    if control is not None:
        (root / "control").write_text(control + "\n")


# --- read_vulns / read_smt --------------------------------------

def test_read_vulns_missing(tmp_path):
    assert mod.read_vulns(str(tmp_path / "nope")) == {}


def test_read_vulns(tmp_path):
    _mk_vulns(tmp_path, {"meltdown": "Not affected",
                            "spectre_v2": "Mitigation: Enhanced IBRS"})
    out = mod.read_vulns(str(tmp_path))
    assert out["meltdown"] == "Not affected"
    assert out["spectre_v2"] == "Mitigation: Enhanced IBRS"


def test_read_smt(tmp_path):
    _mk_smt(tmp_path, active="1", control="on")
    out = mod.read_smt(str(tmp_path))
    assert out["active"] == "1"
    assert out["control"] == "on"


# --- read_cmdline_off_tokens ------------------------------------

def test_read_cmdline_off_tokens_mitigations(tmp_path):
    p = tmp_path / "cmdline"
    p.write_text("ro mitigations=off quiet\n")
    assert mod.read_cmdline_off_tokens(str(p)) == ["mitigations=off"]


def test_read_cmdline_off_tokens_multiple(tmp_path):
    p = tmp_path / "cmdline"
    p.write_text("ro nopti mds=off quiet\n")
    out = mod.read_cmdline_off_tokens(str(p))
    assert set(out) == {"nopti", "mds=off"}


def test_read_cmdline_off_tokens_clean(tmp_path):
    p = tmp_path / "cmdline"
    p.write_text("ro quiet\n")
    assert mod.read_cmdline_off_tokens(str(p)) == []


def test_read_cmdline_off_tokens_missing(tmp_path):
    assert mod.read_cmdline_off_tokens(str(tmp_path / "nope")) == []


# --- classify ---------------------------------------------------

def _smt_off():
    return {"active": "0", "control": "notsupported"}


def _smt_on():
    return {"active": "1", "control": "on"}


def test_classify_unknown():
    v = mod.classify({}, _smt_off(), [])
    assert v["verdict"] == "unknown"


def test_classify_ok():
    v = mod.classify({"meltdown": "Not affected",
                        "spectre_v2": "Mitigation: Enhanced IBRS"},
                       _smt_off(), [])
    assert v["verdict"] == "ok"


def test_classify_vulnerable_unmitigated():
    v = mod.classify({"l1tf": "Vulnerable", "meltdown": "Not affected"},
                       _smt_off(), [])
    assert v["verdict"] == "vulnerable_unmitigated"
    assert "l1tf" in v["reason"]


def test_classify_partial_microcode_only():
    # 'Vulnerable: No microcode' is partial — kernel mitigation
    # in place, microcode needed.
    v = mod.classify({"reg_file_data_sampling": "Vulnerable: No microcode",
                        "meltdown": "Not affected"},
                       _smt_off(), [])
    assert v["verdict"] == "partial_mitigation"


def test_classify_mitigation_disabled():
    v = mod.classify({"meltdown": "Not affected"},
                       _smt_off(), ["mitigations=off"])
    assert v["verdict"] == "mitigation_disabled_via_cmdline"


def test_classify_smt_vuln():
    v = mod.classify(
        {"l1tf": ("Mitigation: PTE Inversion; VMX: cache flushes, "
                    "SMT vulnerable")},
        _smt_on(), [])
    assert v["verdict"] == "smt_forced_on_with_vuln"


def test_classify_priority_unmitigated_wins():
    v = mod.classify(
        {"l1tf": "Vulnerable",
           "reg_file_data_sampling": "Vulnerable: No microcode"},
        _smt_on(), ["mitigations=off"])
    assert v["verdict"] == "vulnerable_unmitigated"


# --- status integration -----------------------------------------

def test_status_unknown(tmp_path):
    out = mod.status(None, str(tmp_path / "novuln"),
                       str(tmp_path / "nosmt"),
                       str(tmp_path / "nocmd"))
    assert out["ok"] is False
    assert out["verdict"]["verdict"] == "unknown"


def test_status_live_like(tmp_path):
    vulns = tmp_path / "vulns"
    smt = tmp_path / "smt"
    cmdline = tmp_path / "cmdline"
    cmdline.write_text("ro quiet\n")
    _mk_vulns(vulns, {"meltdown": "Not affected",
                          "spectre_v2": "Mitigation: Enhanced IBRS",
                          "reg_file_data_sampling": "Vulnerable: No microcode"})
    _mk_smt(smt, active="0", control="notsupported")
    out = mod.status(None, str(vulns), str(smt), str(cmdline))
    assert out["ok"] is True
    assert out["vuln_count"] == 3
    assert out["verdict"]["verdict"] == "partial_mitigation"
