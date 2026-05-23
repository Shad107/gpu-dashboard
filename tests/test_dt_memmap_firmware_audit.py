"""Tests for modules/dt_memmap_firmware_audit.py — R&D #68.4."""
from __future__ import annotations

import os

import pytest

from gpu_dashboard.modules import dt_memmap_firmware_audit as mod


def _mk_memmap_entry(root, n, *, start="0x0", end="0xfff",
                          type_="System RAM"):
    d = root / str(n)
    d.mkdir(parents=True, exist_ok=True)
    (d / "start").write_text(start + "\n")
    (d / "end").write_text(end + "\n")
    (d / "type").write_text(type_ + "\n")


# --- is_devicetree_present --------------------------------------

def test_is_devicetree_present_no(tmp_path):
    assert mod.is_devicetree_present(str(tmp_path / "nope")) is False


def test_is_devicetree_present_yes(tmp_path):
    (tmp_path / "dt").mkdir()
    assert mod.is_devicetree_present(str(tmp_path / "dt")) is True


# --- list_memmap_entries ---------------------------------------

def test_list_memmap_missing(tmp_path):
    assert mod.list_memmap_entries(str(tmp_path / "nope")) == []


def test_list_memmap_three(tmp_path):
    _mk_memmap_entry(tmp_path, 0, type_="System RAM")
    _mk_memmap_entry(tmp_path, 1, type_="Reserved")
    _mk_memmap_entry(tmp_path, 2, type_="ACPI Tables")
    out = mod.list_memmap_entries(str(tmp_path))
    assert [e["type"] for e in out] == \
        ["System RAM", "Reserved", "ACPI Tables"]


def test_list_memmap_sorts_numerically(tmp_path):
    for n in (0, 1, 2, 10, 11):
        _mk_memmap_entry(tmp_path, n, type_=f"E{n}")
    out = mod.list_memmap_entries(str(tmp_path))
    assert [e["id"] for e in out] == ["0", "1", "2", "10", "11"]


# --- vmcoreinfo_state -------------------------------------------

def test_vmcoreinfo_missing(tmp_path):
    out = mod.vmcoreinfo_state(str(tmp_path / "nope"))
    assert out == {"present": False, "readable": False,
                      "bytes_read": 0}


def test_vmcoreinfo_present(tmp_path):
    p = tmp_path / "vmci"
    p.write_text("0x0000000100318000 1024\n")
    out = mod.vmcoreinfo_state(str(p))
    assert out["present"] is True
    assert out["readable"] is True
    assert out["bytes_read"] > 0


def test_vmcoreinfo_empty(tmp_path):
    p = tmp_path / "vmci"
    p.write_text("")
    out = mod.vmcoreinfo_state(str(p))
    assert out["present"] is True
    assert out["readable"] is True
    assert out["bytes_read"] == 0


# --- classify ---------------------------------------------------

def test_classify_unknown_no_surfaces():
    v = mod.classify("x86_64", False, [],
                          {"present": False, "readable": False,
                            "bytes_read": 0})
    assert v["verdict"] == "unknown"


def test_classify_vmcoreinfo_unreadable_absent():
    v = mod.classify("x86_64", False,
                          [{"id": "0", "start": "0x0",
                              "end": "0xfff",
                              "type": "Reserved"}],
                          {"present": False, "readable": False,
                            "bytes_read": 0})
    assert v["verdict"] == "vmcoreinfo_unreadable"


def test_classify_vmcoreinfo_empty():
    v = mod.classify("x86_64", False,
                          [{"id": "0", "type": "Reserved",
                              "start": "0", "end": "0"}],
                          {"present": True, "readable": True,
                            "bytes_read": 0})
    assert v["verdict"] == "vmcoreinfo_unreadable"


def test_classify_memmap_no_reserved():
    v = mod.classify("x86_64", False,
                          [{"id": "0", "type": "System RAM",
                              "start": "0", "end": "0"},
                            {"id": "1", "type": "System RAM",
                              "start": "0", "end": "0"}],
                          {"present": True, "readable": True,
                            "bytes_read": 24})
    assert v["verdict"] == "efi_reserved_regions_zero"


def test_classify_memmap_with_reserved_ok():
    v = mod.classify("x86_64", False,
                          [{"id": "0", "type": "System RAM",
                              "start": "0", "end": "0"},
                            {"id": "1", "type": "Reserved",
                              "start": "0", "end": "0"}],
                          {"present": True, "readable": True,
                            "bytes_read": 24})
    assert v["verdict"] == "ok"


def test_classify_dt_on_x86():
    v = mod.classify("x86_64", True,
                          [{"id": "0", "type": "Reserved",
                              "start": "0", "end": "0"}],
                          {"present": True, "readable": True,
                            "bytes_read": 24})
    assert v["verdict"] == "devicetree_present_on_x86"


def test_classify_dt_on_arm_ok():
    # DT on aarch64 is normal.
    v = mod.classify("aarch64", True,
                          [{"id": "0", "type": "Reserved",
                              "start": "0", "end": "0"}],
                          {"present": True, "readable": True,
                            "bytes_read": 24})
    assert v["verdict"] == "ok"


def test_classify_ok_healthy():
    v = mod.classify("x86_64", False,
                          [{"id": "0", "type": "Reserved",
                              "start": "0", "end": "0"}],
                          {"present": True, "readable": True,
                            "bytes_read": 24})
    assert v["verdict"] == "ok"


# Priority : vmcoreinfo > memmap > dt.
def test_priority_vmcoreinfo_over_memmap():
    v = mod.classify("x86_64", False,
                          [{"id": "0", "type": "System RAM",
                              "start": "0", "end": "0"}],
                          {"present": False, "readable": False,
                            "bytes_read": 0})
    assert v["verdict"] == "vmcoreinfo_unreadable"


def test_priority_memmap_over_dt_on_x86():
    v = mod.classify("x86_64", True,
                          [{"id": "0", "type": "System RAM",
                              "start": "0", "end": "0"}],
                          {"present": True, "readable": True,
                            "bytes_read": 24})
    assert v["verdict"] == "efi_reserved_regions_zero"


# --- status integration -----------------------------------------

def test_status_unknown(tmp_path):
    out = mod.status(None,
                          str(tmp_path / "no_dt"),
                          str(tmp_path / "no_memmap"),
                          str(tmp_path / "no_vmci"))
    assert out["ok"] is False
    assert out["verdict"]["verdict"] == "unknown"


def test_status_ok_synthetic(tmp_path):
    memmap = tmp_path / "memmap"; memmap.mkdir()
    _mk_memmap_entry(memmap, 0, type_="System RAM")
    _mk_memmap_entry(memmap, 1, type_="Reserved")
    vmci = tmp_path / "vmci"
    vmci.write_text("0x0000 1024\n")
    out = mod.status(None,
                          str(tmp_path / "no_dt"),
                          str(memmap),
                          str(vmci))
    assert out["ok"] is True
    assert out["memmap_entry_count"] == 2
    assert out["vmcoreinfo_bytes"] > 0
    assert out["verdict"]["verdict"] == "ok"


def test_status_live_smoke():
    out = mod.status(None)
    assert out["verdict"]["verdict"] in (
        "ok", "vmcoreinfo_unreadable",
        "efi_reserved_regions_zero",
        "devicetree_present_on_x86", "unknown")
