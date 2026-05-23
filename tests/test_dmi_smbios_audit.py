"""Tests for modules/dmi_smbios_audit.py — R&D #59.1."""
from __future__ import annotations

import datetime
import pytest

from gpu_dashboard.modules import dmi_smbios_audit as mod


def _mk_dmi(root, **fields):
    root.mkdir(parents=True, exist_ok=True)
    for k, v in fields.items():
        (root / k).write_text(v + "\n")


# --- parse_bios_date --------------------------------------------

def test_parse_bios_date():
    d = mod.parse_bios_date("07/10/2025")
    assert d == datetime.date(2025, 7, 10)


def test_parse_bios_date_invalid():
    assert mod.parse_bios_date("not-a-date") is None
    assert mod.parse_bios_date(None) is None
    assert mod.parse_bios_date("") is None


# --- is_vm_vendor -----------------------------------------------

def test_is_vm_vendor():
    assert mod.is_vm_vendor("QEMU") is True
    assert mod.is_vm_vendor("VMware, Inc.") is True
    assert mod.is_vm_vendor("innotek GmbH") is True  # VirtualBox
    assert mod.is_vm_vendor("Microsoft Corporation") is True
    assert mod.is_vm_vendor("ASUSTeK COMPUTER INC.") is False
    assert mod.is_vm_vendor("") is False
    assert mod.is_vm_vendor(None) is False


# --- read_dmi ---------------------------------------------------

def test_read_dmi_missing(tmp_path):
    assert mod.read_dmi(str(tmp_path / "nope")) == {}


def test_read_dmi_present(tmp_path):
    _mk_dmi(tmp_path, sys_vendor="ASUSTeK COMPUTER INC.",
              product_name="ROG STRIX X670E",
              bios_vendor="American Megatrends",
              bios_version="2104",
              bios_date="06/12/2024")
    out = mod.read_dmi(str(tmp_path))
    assert out["sys_vendor"] == "ASUSTeK COMPUTER INC."
    assert out["bios_date"] == "06/12/2024"


# --- classify ---------------------------------------------------

def _today(year=2026, month=5, day=23):
    return datetime.date(year, month, day)


def test_classify_dmi_absent():
    v = mod.classify({})
    assert v["verdict"] == "dmi_absent"


def test_classify_ok_recent():
    dmi = {"sys_vendor": "ASUSTeK COMPUTER INC.",
              "product_name": "ROG STRIX",
              "board_vendor": "ASUSTeK COMPUTER INC.",
              "bios_date": "06/12/2024",
              "bios_version": "2104"}
    v = mod.classify(dmi, _today())
    assert v["verdict"] == "ok"


def test_classify_bios_stale():
    dmi = {"sys_vendor": "ASUSTeK COMPUTER INC.",
              "board_vendor": "ASUSTeK COMPUTER INC.",
              "bios_date": "06/12/2021"}  # 5 years old vs 2026 today
    v = mod.classify(dmi, _today())
    assert v["verdict"] == "bios_stale_gt_3y"


def test_classify_qemu():
    dmi = {"sys_vendor": "QEMU",
              "product_name": "Standard PC (Q35 + ICH9, 2009)",
              "bios_date": "07/10/2025"}
    v = mod.classify(dmi, _today())
    assert v["verdict"] == "qemu_or_vm_detected"


def test_classify_board_unknown():
    dmi = {"sys_vendor": None, "board_vendor": None,
              "bios_date": None}
    v = mod.classify(dmi, _today())
    assert v["verdict"] == "board_unknown"


def test_classify_priority_stale_wins_over_vm():
    # If bios date is stale AND vendor is QEMU, stale wins.
    dmi = {"sys_vendor": "QEMU", "bios_date": "06/12/2021"}
    v = mod.classify(dmi, _today())
    assert v["verdict"] == "bios_stale_gt_3y"


# --- status integration -----------------------------------------

def test_status_absent(tmp_path):
    out = mod.status(None, str(tmp_path / "nope"))
    assert out["ok"] is False
    assert out["verdict"]["verdict"] == "dmi_absent"


def test_status_qemu(tmp_path):
    _mk_dmi(tmp_path, sys_vendor="QEMU",
              product_name="Standard PC (Q35 + ICH9, 2009)",
              bios_vendor="Proxmox distribution of EDK II",
              bios_version="4.2025.02",
              bios_date="07/10/2025")
    out = mod.status(None, str(tmp_path), _today())
    assert out["ok"] is True
    assert out["is_vm"] is True
    assert out["verdict"]["verdict"] == "qemu_or_vm_detected"
