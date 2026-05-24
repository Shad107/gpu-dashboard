"""Tests for modules/pcie_dpc_audit.py — R&D #90.4."""
from __future__ import annotations

import pytest

from gpu_dashboard.modules import pcie_dpc_audit as mod


def _mk_device(tmp_path, bdf, *, dpc=True, cap="0x101",
                ctl="1", status="0",
                status_unreadable=False):
    base = tmp_path / "pci" / bdf
    base.mkdir(parents=True, exist_ok=True)
    if dpc:
        d = base / "dpc"
        d.mkdir(exist_ok=True)
        (d / "dpc_cap").write_text(cap + "\n")
        (d / "dpc_ctl").write_text(ctl + "\n")
        if not status_unreadable:
            (d / "dpc_status").write_text(status + "\n")
    return str(base)


# --- parse_dpc_value -------------------------------------------

def test_parse_dpc_value_decimal():
    assert mod.parse_dpc_value("1") == 1
    assert mod.parse_dpc_value("0") == 0


def test_parse_dpc_value_hex():
    assert mod.parse_dpc_value("0x101") == 257
    assert mod.parse_dpc_value("0X1") == 1


def test_parse_dpc_value_empty():
    assert mod.parse_dpc_value("") is None
    assert mod.parse_dpc_value("garbage") is None


# --- read_dpc --------------------------------------------------

def test_read_dpc_no_dpc_subdir(tmp_path):
    base = tmp_path / "pci" / "0000:00:00.0"
    base.mkdir(parents=True)
    out = mod.read_dpc(str(tmp_path / "pci"),
                          "0000:00:00.0")
    assert out["has_dpc"] is False


def test_read_dpc_present(tmp_path):
    _mk_device(tmp_path, "0000:00:1c.0",
                    cap="0x101", ctl="1", status="0")
    out = mod.read_dpc(str(tmp_path / "pci"),
                          "0000:00:1c.0")
    assert out["has_dpc"] is True
    assert out["cap"] == 257
    assert out["ctl"] == 1
    assert out["status"] == 0


def test_read_dpc_triggered(tmp_path):
    _mk_device(tmp_path, "0000:00:1c.0",
                    cap="0x101", ctl="1", status="0x1f")
    out = mod.read_dpc(str(tmp_path / "pci"),
                          "0000:00:1c.0")
    assert out["status"] == 31


# --- classify --------------------------------------------------

def _dev(*, bdf="d", has_dpc=False, cap=None, ctl=None,
         status=None, status_readable=True):
    return {"bdf": bdf, "has_dpc": has_dpc, "cap": cap,
            "ctl": ctl, "status": status,
            "status_readable": status_readable}


def test_classify_unknown_no_devices():
    v = mod.classify([])
    assert v["verdict"] == "unknown"


def test_classify_ok_no_dpc_capable():
    v = mod.classify([
        _dev(bdf="d1"), _dev(bdf="d2"),
    ])
    assert v["verdict"] == "ok"
    assert "no dpc-capable" in v["reason"].lower()


def test_classify_ok_all_enabled_quiet():
    v = mod.classify([
        _dev(bdf="d1", has_dpc=True, cap=0x101,
             ctl=1, status=0),
        _dev(bdf="d2", has_dpc=True, cap=0x101,
             ctl=1, status=0),
    ])
    assert v["verdict"] == "ok"


def test_classify_dpc_triggered():
    v = mod.classify([
        _dev(bdf="d1", has_dpc=True, cap=0x101,
             ctl=1, status=0x1f),
        _dev(bdf="d2", has_dpc=True, cap=0x101,
             ctl=1, status=0),
    ])
    assert v["verdict"] == "dpc_triggered"
    assert v["ports"] == ["d1"]


def test_classify_requires_root_unreadable_status():
    v = mod.classify([
        _dev(bdf="d1", has_dpc=True, cap=0x101,
             ctl=1, status=None, status_readable=False),
    ])
    assert v["verdict"] == "requires_root"


def test_classify_dpc_disabled_capable():
    v = mod.classify([
        _dev(bdf="d1", has_dpc=True, cap=0x101,
             ctl=0, status=0),
        _dev(bdf="d2", has_dpc=True, cap=0x101,
             ctl=0, status=0),
    ])
    assert v["verdict"] == "dpc_disabled_capable"


# Priority : triggered > requires_root > disabled_capable
def test_priority_triggered_over_disabled():
    v = mod.classify([
        _dev(bdf="d1", has_dpc=True, cap=0x101,
             ctl=0, status=0),
        _dev(bdf="d2", has_dpc=True, cap=0x101,
             ctl=1, status=0x1f),
    ])
    assert v["verdict"] == "dpc_triggered"


# --- status integration ----------------------------------------

def test_status_unknown(tmp_path):
    out = mod.status(None, str(tmp_path / "nope"))
    assert out["verdict"]["verdict"] == "unknown"


def test_status_ok_no_dpc_synthetic(tmp_path):
    base = tmp_path / "pci" / "0000:00:00.0"
    base.mkdir(parents=True)
    out = mod.status(None, str(tmp_path / "pci"))
    assert out["verdict"]["verdict"] == "ok"
    assert out["dpc_capable_count"] == 0


def test_status_dpc_triggered_synthetic(tmp_path):
    _mk_device(tmp_path, "0000:00:1c.0",
                    cap="0x101", ctl="1", status="0x1f")
    out = mod.status(None, str(tmp_path / "pci"))
    assert out["verdict"]["verdict"] == "dpc_triggered"
    assert out["ok"] is False


def test_status_disabled_capable_synthetic(tmp_path):
    _mk_device(tmp_path, "0000:00:1c.0",
                    cap="0x101", ctl="0", status="0")
    out = mod.status(None, str(tmp_path / "pci"))
    assert (out["verdict"]["verdict"]
            == "dpc_disabled_capable")
    assert out["dpc_capable_count"] == 1
