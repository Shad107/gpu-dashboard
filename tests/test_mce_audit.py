"""Tests for modules/mce_audit.py — R&D #47.4."""
from __future__ import annotations

from pathlib import Path

import pytest

from gpu_dashboard.modules import mce_audit as mod


def _mk_mc(root: Path, cpu: int, *,
            check_interval: int = 300,
            cmci_disabled: int = 0, ignore_ce: int = 0,
            dont_log_ce: int = 0, monarch_timeout: int = 1000000,
            tolerant: int | None = None,
            banks: dict | None = None):
    d = root / f"machinecheck{cpu}"
    d.mkdir(parents=True, exist_ok=True)
    (d / "check_interval").write_text(str(check_interval) + "\n")
    (d / "cmci_disabled").write_text(str(cmci_disabled) + "\n")
    (d / "ignore_ce").write_text(str(ignore_ce) + "\n")
    (d / "dont_log_ce").write_text(str(dont_log_ce) + "\n")
    (d / "monarch_timeout").write_text(str(monarch_timeout) + "\n")
    if tolerant is not None:
        (d / "tolerant").write_text(str(tolerant) + "\n")
    for idx, mask in (banks or {}).items():
        (d / f"bank{idx}").write_text(str(mask) + "\n")


# --- list_cpus -----------------------------------------------------

def test_list_cpus_numeric_sort(tmp_path):
    for n in [0, 1, 10, 2]:
        _mk_mc(tmp_path, n)
    assert mod.list_cpus(str(tmp_path)) == [0, 1, 2, 10]


def test_list_cpus_missing(tmp_path):
    assert mod.list_cpus(str(tmp_path / "nope")) == []


# --- read_cpu_mce -------------------------------------------------

def test_read_cpu_mce_basic(tmp_path):
    _mk_mc(tmp_path, 0, check_interval=300, cmci_disabled=0,
             ignore_ce=0, banks={0: 0xffffffff, 4: 0xffffffff})
    r = mod.read_cpu_mce(str(tmp_path), 0)
    assert r["cpu"] == 0
    assert r["check_interval"] == 300
    assert r["banks"][0] == 0xffffffff
    assert r["banks"][4] == 0xffffffff


def test_read_cpu_mce_hex_bank(tmp_path):
    d = tmp_path / "machinecheck0"
    d.mkdir(parents=True)
    (d / "bank0").write_text("0xff\n")
    r = mod.read_cpu_mce(str(tmp_path), 0)
    assert r["banks"][0] == 0xff


# --- is_uniform ---------------------------------------------------

def test_is_uniform_true():
    rows = [{"ignore_ce": 0}, {"ignore_ce": 0}, {"ignore_ce": 0}]
    assert mod.is_uniform(rows, "ignore_ce") is True


def test_is_uniform_false():
    rows = [{"ignore_ce": 0}, {"ignore_ce": 1}]
    assert mod.is_uniform(rows, "ignore_ce") is False


# --- classify ------------------------------------------------------

def _row(**o):
    base = {"cpu": 0, "check_interval": 300,
              "cmci_disabled": 0, "ignore_ce": 0,
              "dont_log_ce": 0, "monarch_timeout": 1000000,
              "banks": {0: 0xffffffff, 4: 0xffffffff}}
    base.update(o)
    return base


def test_classify_no_mce():
    v = mod.classify([])
    assert v["verdict"] == "no_mce"


def test_classify_ok():
    v = mod.classify([_row()])
    assert v["verdict"] == "ok"


def test_classify_ignore_ce():
    v = mod.classify([_row(ignore_ce=1)])
    assert v["verdict"] == "ignore_ce_masked"


def test_classify_dont_log_ce():
    v = mod.classify([_row(dont_log_ce=1)])
    assert v["verdict"] == "ignore_ce_masked"


def test_classify_tolerant_high():
    v = mod.classify([_row(tolerant=3)])
    assert v["verdict"] == "tolerant_too_high"


def test_classify_cmci_disabled():
    v = mod.classify([_row(cmci_disabled=1)])
    assert v["verdict"] == "cmci_disabled_intel"


def test_classify_bank_silenced():
    v = mod.classify([_row(banks={0: 0xffffffff, 4: 0})])
    assert v["verdict"] == "bank_silenced"
    assert "4" in v["reason"]


def test_classify_priority_ignore_wins():
    v = mod.classify([_row(ignore_ce=1, tolerant=3)])
    assert v["verdict"] == "ignore_ce_masked"


def test_classify_priority_tolerant_over_cmci():
    v = mod.classify([_row(tolerant=2, cmci_disabled=1)])
    assert v["verdict"] == "tolerant_too_high"


# --- status integration -------------------------------------------

def test_status_with_isolated(monkeypatch, tmp_path):
    sys_mce = tmp_path / "mce"
    sys_mce.mkdir()
    for cpu in range(4):
        _mk_mc(sys_mce, cpu, banks={0: 0xffffffff, 4: 0xffffffff})
    monkeypatch.setattr(mod, "_SYS_MCE", str(sys_mce))
    out = mod.status()
    assert out["ok"] is True
    assert out["cpu_count"] == 4
    assert out["verdict"]["verdict"] == "ok"


def test_status_no_mce(monkeypatch, tmp_path):
    monkeypatch.setattr(mod, "_SYS_MCE", str(tmp_path / "nope"))
    out = mod.status()
    assert out["ok"] is False
    assert out["verdict"]["verdict"] == "no_mce"


def test_status_ignore_ce_flagged(monkeypatch, tmp_path):
    sys_mce = tmp_path / "mce"
    sys_mce.mkdir()
    _mk_mc(sys_mce, 0, ignore_ce=1)
    monkeypatch.setattr(mod, "_SYS_MCE", str(sys_mce))
    out = mod.status()
    assert out["verdict"]["verdict"] == "ignore_ce_masked"
