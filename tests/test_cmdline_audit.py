"""Tests for modules/cmdline_audit.py — kernel cmdline auditor."""
from __future__ import annotations

from pathlib import Path

import pytest

from gpu_dashboard.modules import cmdline_audit


def _mk_cmdline(path: Path, text: str):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text + "\n")


# --- parse_cmdline ----------------------------------------------

def test_parse_cmdline_basic():
    out = cmdline_audit.parse_cmdline(
        "BOOT_IMAGE=/boot/vmlinuz-6.17 root=UUID=abc ro quiet")
    assert out["BOOT_IMAGE"] == "/boot/vmlinuz-6.17"
    assert out["root"] == "UUID=abc"
    assert out["ro"] is True
    assert out["quiet"] is True


def test_parse_cmdline_with_dotted_keys():
    out = cmdline_audit.parse_cmdline(
        "intel_pstate=passive cpufreq.default_governor=performance")
    assert out["intel_pstate"] == "passive"
    assert out["cpufreq.default_governor"] == "performance"


def test_parse_cmdline_with_complex_value():
    out = cmdline_audit.parse_cmdline(
        "crashkernel=2G-4G:320M,4G-32G:512M mitigations=off")
    assert "320M" in out["crashkernel"]
    assert out["mitigations"] == "off"


def test_parse_cmdline_empty():
    assert cmdline_audit.parse_cmdline("") == {}


def test_parse_cmdline_quoted_value():
    # Some kernels quote arguments
    out = cmdline_audit.parse_cmdline('foo="bar baz" qux=1')
    assert out["foo"] == "bar baz"
    assert out["qux"] == "1"


# --- flag categorization ---------------------------------------

def test_categorize_mitigations_off_is_safety_disabled():
    cats = cmdline_audit.categorize_flags({"mitigations": "off"})
    assert "safety_disabled" in cats
    assert any("mitigations" in f["key"] for f in cats["safety_disabled"])


def test_categorize_nosmt_is_safety_disabled():
    cats = cmdline_audit.categorize_flags({"nosmt": True})
    assert "safety_disabled" in cats


def test_categorize_isolcpus_is_perf():
    cats = cmdline_audit.categorize_flags({"isolcpus": "8-15"})
    assert "perf_oriented" in cats


def test_categorize_idle_poll_is_perf():
    cats = cmdline_audit.categorize_flags({"idle": "poll"})
    assert "perf_oriented" in cats


def test_categorize_transparent_hugepage_always_is_perf():
    cats = cmdline_audit.categorize_flags(
        {"transparent_hugepage": "always"})
    assert "perf_oriented" in cats


def test_categorize_intel_pstate_disable_is_power():
    cats = cmdline_audit.categorize_flags({"intel_pstate": "disable"})
    assert "power" in cats


def test_categorize_pci_nomsi_is_virt():
    cats = cmdline_audit.categorize_flags({"pci": "nomsi"})
    assert "virt_pinning" in cats


def test_categorize_quiet_is_ignored():
    # Boring flags shouldn't end up in any category
    cats = cmdline_audit.categorize_flags({"quiet": True, "ro": True})
    assert all(len(v) == 0 for v in cats.values())


def test_categorize_BOOT_IMAGE_is_ignored():
    cats = cmdline_audit.categorize_flags(
        {"BOOT_IMAGE": "/boot/vmlinuz-6.17", "root": "UUID=abc"})
    assert all(len(v) == 0 for v in cats.values())


# --- classify --------------------------------------------------

def test_classify_clean_default_ubuntu():
    flags = {"BOOT_IMAGE": "/boot/vmlinuz-6.17", "root": "UUID=abc",
             "ro": True, "quiet": True}
    cats = cmdline_audit.categorize_flags(flags)
    v = cmdline_audit.classify(cats)
    assert v["verdict"] == "clean"


def test_classify_perf_tuned():
    flags = {"isolcpus": "8-15", "idle": "poll"}
    cats = cmdline_audit.categorize_flags(flags)
    v = cmdline_audit.classify(cats)
    assert v["verdict"] == "perf_tuned"


def test_classify_safety_disabled_warns():
    flags = {"mitigations": "off"}
    cats = cmdline_audit.categorize_flags(flags)
    v = cmdline_audit.classify(cats)
    assert v["verdict"] == "safety_disabled"
    assert "mitigations" in v["reason"].lower()


def test_classify_mixed():
    flags = {"mitigations": "off", "isolcpus": "8-15"}
    cats = cmdline_audit.categorize_flags(flags)
    v = cmdline_audit.classify(cats)
    # Safety wins as the worst
    assert v["verdict"] == "safety_disabled"


# --- status ---------------------------------------------------

def test_status_clean_default(tmp_path, monkeypatch):
    cmd = tmp_path / "cmdline"
    _mk_cmdline(cmd,
                 "BOOT_IMAGE=/boot/vmlinuz-6.17 root=UUID=abc ro quiet")
    monkeypatch.setattr(cmdline_audit, "_CMDLINE_PATH", str(cmd))
    s = cmdline_audit.status()
    assert s["ok"] is True
    assert s["raw"].startswith("BOOT_IMAGE=")
    assert s["verdict"]["verdict"] == "clean"


def test_status_live_with_crashkernel(tmp_path, monkeypatch):
    # The live-rig case
    cmd = tmp_path / "cmdline"
    _mk_cmdline(cmd,
                 "BOOT_IMAGE=/boot/vmlinuz-6.17.0 root=UUID=abc ro "
                 "crashkernel=2G-4G:320M,4G-32G:512M")
    monkeypatch.setattr(cmdline_audit, "_CMDLINE_PATH", str(cmd))
    s = cmdline_audit.status()
    # crashkernel is normal Ubuntu/Debian default, not flagged
    assert s["verdict"]["verdict"] == "clean"
    assert "crashkernel" in s["flags"]


def test_status_with_mitigations_off(tmp_path, monkeypatch):
    cmd = tmp_path / "cmdline"
    _mk_cmdline(cmd,
                 "BOOT_IMAGE=/boot/x root=UUID=abc ro mitigations=off")
    monkeypatch.setattr(cmdline_audit, "_CMDLINE_PATH", str(cmd))
    s = cmdline_audit.status()
    assert s["verdict"]["verdict"] == "safety_disabled"
    assert "safety_disabled" in s["categories"]


def test_status_missing_cmdline(tmp_path, monkeypatch):
    monkeypatch.setattr(cmdline_audit, "_CMDLINE_PATH",
                          str(tmp_path / "absent"))
    s = cmdline_audit.status()
    assert s["ok"] is False
    assert s["error"] == "cmdline_unavailable"


def test_status_exposes_interesting_flags(tmp_path, monkeypatch):
    cmd = tmp_path / "cmdline"
    _mk_cmdline(cmd,
                 "BOOT_IMAGE=/boot/x root=UUID=abc ro "
                 "isolcpus=8-15 idle=poll transparent_hugepage=always")
    monkeypatch.setattr(cmdline_audit, "_CMDLINE_PATH", str(cmd))
    s = cmdline_audit.status()
    interesting = s["categories"]["perf_oriented"]
    keys = [f["key"] for f in interesting]
    assert "isolcpus" in keys
    assert "idle" in keys
    assert "transparent_hugepage" in keys
