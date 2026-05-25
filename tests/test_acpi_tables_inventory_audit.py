"""Tests for modules/acpi_tables_inventory_audit.py R&D #109.3."""
from __future__ import annotations

import pytest

from gpu_dashboard.modules import acpi_tables_inventory_audit as mod


def _mk_tables(root, table_dict):
    """table_dict: {name: size_bytes}."""
    d = root / "tables"
    d.mkdir(parents=True, exist_ok=True)
    for name, size in table_dict.items():
        (d / name).write_bytes(b"\x00" * size)


def test_count_ssdts():
    assert mod.count_ssdts(set()) == 0
    assert mod.count_ssdts({"SSDT"}) == 1
    assert mod.count_ssdts({"SSDT", "SSDT2", "SSDT3"}) == 3
    assert mod.count_ssdts({"DSDT", "APIC"}) == 0


def test_classify_unknown():
    v = mod.classify(False, False, {}, False)
    assert v["verdict"] == "unknown"


def test_classify_requires_root():
    v = mod.classify(True, False, {}, False)
    assert v["verdict"] == "requires_root"


def test_classify_ok():
    v = mod.classify(True, True,
                          {"names": {"DSDT", "APIC",
                                       "SSDT", "SRAT"},
                           "sizes": {"DSDT": 30000}},
                          False)
    assert v["verdict"] == "ok"


def test_classify_missing_srat_multinode_warn():
    v = mod.classify(True, True,
                          {"names": {"DSDT", "APIC"},
                           "sizes": {"DSDT": 30000}},
                          True)
    assert v["verdict"] == "missing_srat_with_multinode"


def test_classify_huge_dsdt_accent():
    v = mod.classify(True, True,
                          {"names": {"DSDT", "APIC", "SRAT"},
                           "sizes": {"DSDT": 300_000}},
                          False)
    assert v["verdict"] == "huge_dsdt"


def test_classify_excess_ssdts_accent():
    names = {"DSDT", "APIC"}
    for i in range(25):
        names.add(f"SSDT{i}" if i else "SSDT")
    v = mod.classify(True, True,
                          {"names": names,
                           "sizes": {"DSDT": 30000}},
                          False)
    assert v["verdict"] == "excess_ssdts"


def test_status_unknown(tmp_path):
    out = mod.status(None,
                       str(tmp_path / "nope"),
                       str(tmp_path / "no_node"))
    assert out["verdict"]["verdict"] == "unknown"


def test_status_ok_synthetic(tmp_path):
    _mk_tables(tmp_path, {
        "DSDT": 30000, "APIC": 200,
        "SSDT": 5000, "SRAT": 400})
    nm = tmp_path / "online"
    nm.write_text("0\n")
    out = mod.status(None, str(tmp_path / "tables"),
                       str(nm))
    assert out["verdict"]["verdict"] == "ok"
    assert out["table_count"] == 4
    assert out["dsdt_size"] == 30000
    assert out["has_srat"] is True


def test_status_huge_dsdt(tmp_path):
    _mk_tables(tmp_path, {
        "DSDT": 300_000, "APIC": 200,
        "SRAT": 400})
    nm = tmp_path / "online"
    nm.write_text("0\n")
    out = mod.status(None, str(tmp_path / "tables"),
                       str(nm))
    assert out["verdict"]["verdict"] == "huge_dsdt"
