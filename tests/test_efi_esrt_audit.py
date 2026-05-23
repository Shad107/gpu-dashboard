"""Tests for modules/efi_esrt_audit.py — R&D #67.1."""
from __future__ import annotations

import os

import pytest

from gpu_dashboard.modules import efi_esrt_audit as mod


def _mk_esrt(root, *, fw_resource_count=0,
                  fw_resource_count_max=8,
                  fw_resource_version=1):
    root.mkdir(parents=True, exist_ok=True)
    (root / "fw_resource_count").write_text(
        f"{fw_resource_count}\n")
    (root / "fw_resource_count_max").write_text(
        f"{fw_resource_count_max}\n")
    (root / "fw_resource_version").write_text(
        f"{fw_resource_version}\n")
    (root / "entries").mkdir(exist_ok=True)


def _mk_entry(esrt_root, name, *,
                  fw_class="aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee",
                  fw_type=1,
                  fw_version=0x00010000,
                  lowest_supported_fw_version=0x00010000,
                  capsule_flags=0x0,
                  last_attempt_status=0,
                  last_attempt_version=0x00010000):
    d = esrt_root / "entries" / name
    d.mkdir(parents=True, exist_ok=True)
    (d / "fw_class").write_text(fw_class + "\n")
    (d / "fw_type").write_text(f"{fw_type}\n")
    (d / "fw_version").write_text(f"{fw_version}\n")
    (d / "lowest_supported_fw_version").write_text(
        f"{lowest_supported_fw_version}\n")
    (d / "capsule_flags").write_text(f"{capsule_flags}\n")
    (d / "last_attempt_status").write_text(
        f"{last_attempt_status}\n")
    (d / "last_attempt_version").write_text(
        f"{last_attempt_version}\n")


# --- read_esrt_header -------------------------------------------

def test_read_esrt_header_missing(tmp_path):
    out = mod.read_esrt_header(str(tmp_path / "nope"))
    assert out["fw_resource_count"] is None
    assert out["fw_resource_count_max"] is None


def test_read_esrt_header_populated(tmp_path):
    esrt = tmp_path / "esrt"
    _mk_esrt(esrt, fw_resource_count=3, fw_resource_count_max=8,
                fw_resource_version=2)
    out = mod.read_esrt_header(str(esrt))
    assert out["fw_resource_count"] == 3
    assert out["fw_resource_count_max"] == 8
    assert out["fw_resource_version"] == 2


# --- list_entries -----------------------------------------------

def test_list_entries_missing(tmp_path):
    assert mod.list_entries(str(tmp_path / "nope")) == []


def test_list_entries(tmp_path):
    esrt = tmp_path / "esrt"
    _mk_esrt(esrt, fw_resource_count=2)
    _mk_entry(esrt, "entry0", fw_class="aaa")
    _mk_entry(esrt, "entry1", fw_class="bbb")
    out = mod.list_entries(str(esrt))
    assert len(out) == 2
    assert sorted(e["fw_class"] for e in out) == ["aaa", "bbb"]


# --- classify ---------------------------------------------------

def test_classify_unknown_no_efi():
    v = mod.classify(False, False, {}, [])
    assert v["verdict"] == "unknown"


def test_classify_no_esrt_support():
    v = mod.classify(True, False, {}, [])
    assert v["verdict"] == "no_esrt_support"


def test_classify_esrt_empty():
    v = mod.classify(True, True,
                          {"fw_resource_count": 0,
                            "fw_resource_count_max": 8,
                            "fw_resource_version": 1},
                          [])
    assert v["verdict"] == "esrt_empty"


def test_classify_ok():
    v = mod.classify(True, True,
                          {"fw_resource_count": 2,
                            "fw_resource_count_max": 8,
                            "fw_resource_version": 1},
                          [{"id": "entry0",
                              "fw_class": "x",
                              "fw_version": 0x10000,
                              "lowest_supported_fw_version": 0x10000,
                              "last_attempt_status": 0,
                              "last_attempt_version": 0x10000}])
    assert v["verdict"] == "ok"


def test_classify_last_capsule_failed():
    v = mod.classify(True, True,
                          {"fw_resource_count": 1},
                          [{"id": "entry0",
                              "fw_class": "x",
                              "fw_version": 0x10000,
                              "lowest_supported_fw_version": 0x10000,
                              "last_attempt_status": 5,
                              "last_attempt_version": 0x10000}])
    assert v["verdict"] == "last_capsule_failed"
    assert "5" in v["reason"]


def test_classify_stale_components():
    v = mod.classify(True, True,
                          {"fw_resource_count": 1},
                          [{"id": "entry0",
                              "fw_class": "x",
                              "fw_version": 0x10000,
                              "lowest_supported_fw_version": 0x20000,
                              "last_attempt_status": 0,
                              "last_attempt_version": 0x10000}])
    assert v["verdict"] == "stale_firmware_components"


# Priority: last_capsule_failed beats stale_firmware_components.
def test_priority_failed_over_stale():
    v = mod.classify(True, True,
                          {"fw_resource_count": 1},
                          [{"id": "entry0",
                              "fw_class": "x",
                              "fw_version": 0x10000,
                              "lowest_supported_fw_version": 0x20000,
                              "last_attempt_status": 1,
                              "last_attempt_version": 0x10000}])
    assert v["verdict"] == "last_capsule_failed"


# Entries with missing lowest_supported_fw_version aren't flagged
# stale (the kernel sometimes omits the file on older boards).
def test_classify_missing_lowest_supported_no_stale():
    v = mod.classify(True, True,
                          {"fw_resource_count": 1},
                          [{"id": "entry0",
                              "fw_class": "x",
                              "fw_version": 0x10000,
                              "lowest_supported_fw_version": None,
                              "last_attempt_status": 0,
                              "last_attempt_version": 0x10000}])
    assert v["verdict"] == "ok"


# --- status integration -----------------------------------------

def test_status_unknown(tmp_path):
    # Neither efi nor esrt present.
    out = mod.status(None,
                          str(tmp_path / "no-efi"),
                          str(tmp_path / "no-esrt"))
    assert out["ok"] is False
    assert out["verdict"]["verdict"] == "unknown"


def test_status_no_esrt_support(tmp_path):
    # /sys/firmware/efi present but no /sys/firmware/efi/esrt.
    efi = tmp_path / "efi"; efi.mkdir()
    out = mod.status(None, str(efi),
                          str(efi / "esrt"))
    assert out["ok"] is True
    assert out["esrt_present"] is False
    assert out["verdict"]["verdict"] == "no_esrt_support"


def test_status_ok_live_like(tmp_path):
    efi = tmp_path / "efi"; efi.mkdir()
    esrt = efi / "esrt"
    _mk_esrt(esrt, fw_resource_count=2)
    _mk_entry(esrt, "entry0",
                 fw_version=0x20000,
                 lowest_supported_fw_version=0x10000,
                 last_attempt_status=0)
    _mk_entry(esrt, "entry1",
                 fw_version=0x30000,
                 lowest_supported_fw_version=0x10000,
                 last_attempt_status=0)
    out = mod.status(None, str(efi), str(esrt))
    assert out["ok"] is True
    assert out["esrt_present"] is True
    assert out["entry_count"] == 2
    assert out["verdict"]["verdict"] == "ok"
