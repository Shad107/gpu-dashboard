"""Tests for modules/dmi_bios.py — R&D #30.5 DMI/BIOS revision tracker."""
from __future__ import annotations

import json
import os
import tempfile
from unittest import mock

import pytest

from gpu_dashboard.modules import dmi_bios


@pytest.fixture
def fake_dmi(tmp_path):
    """Build a fake /sys/devices/virtual/dmi/id tree."""
    root = tmp_path / "dmi"
    root.mkdir()
    return root


def _write(root, **fields):
    for k, v in fields.items():
        (root / k).write_text(v)


def test_read_field_returns_content(fake_dmi):
    _write(fake_dmi, bios_version="F16")
    assert dmi_bios.read_field(str(fake_dmi), "bios_version") == "F16"


def test_read_field_strips_whitespace(fake_dmi):
    _write(fake_dmi, bios_version="  F16\n")
    assert dmi_bios.read_field(str(fake_dmi), "bios_version") == "F16"


def test_read_field_missing_returns_none(fake_dmi):
    assert dmi_bios.read_field(str(fake_dmi), "board_name") is None


def test_read_field_unreadable_returns_none(fake_dmi):
    # Path that's a directory not a file
    (fake_dmi / "weird").mkdir()
    assert dmi_bios.read_field(str(fake_dmi), "weird") is None


def test_read_dmi_collects_all_known_fields(fake_dmi):
    _write(fake_dmi,
           bios_version="F16",
           bios_date="09/15/2024",
           bios_vendor="American Megatrends",
           board_name="X570 AORUS ELITE",
           board_vendor="Gigabyte",
           sys_vendor="Gigabyte",
           product_name="X570 AORUS ELITE")
    d = dmi_bios.read_dmi(str(fake_dmi))
    assert d["bios_version"] == "F16"
    assert d["board_name"] == "X570 AORUS ELITE"
    assert d["sys_vendor"] == "Gigabyte"


def test_read_dmi_handles_missing_files(fake_dmi):
    _write(fake_dmi, bios_version="F16", sys_vendor="QEMU")
    d = dmi_bios.read_dmi(str(fake_dmi))
    assert d["bios_version"] == "F16"
    assert d["board_name"] is None
    assert d["sys_vendor"] == "QEMU"


def test_parse_bios_date_us_format():
    # DMI typically uses MM/DD/YYYY
    assert dmi_bios.parse_bios_date("09/15/2024") == "2024-09-15"


def test_parse_bios_date_with_leading_zeros():
    assert dmi_bios.parse_bios_date("07/10/2025") == "2025-07-10"


def test_parse_bios_date_invalid_returns_none():
    assert dmi_bios.parse_bios_date("") is None
    assert dmi_bios.parse_bios_date("garbage") is None
    assert dmi_bios.parse_bios_date(None) is None


def test_classify_no_board_name_is_unknown():
    dmi = {"bios_version": "1.0", "board_name": None, "sys_vendor": "QEMU"}
    v = dmi_bios.classify(dmi, catalog={})
    assert v["verdict"] == "unknown"
    assert "board" in v["reason"].lower()


def test_classify_board_not_in_catalog_is_unknown_board():
    dmi = {"bios_version": "F16",
           "board_name": "Wholly New Board",
           "sys_vendor": "Gigabyte"}
    v = dmi_bios.classify(dmi, catalog={})
    assert v["verdict"] == "unknown_board"
    assert "catalog" in v["reason"].lower()


def test_classify_outdated_for_rebar():
    catalog = {
        "X570 AORUS ELITE": {
            "min_rebar": "F16",
            "min_aer": "F15",
            "vendor_url": "https://example.com/x570",
        },
    }
    dmi = {"bios_version": "F11",
           "board_name": "X570 AORUS ELITE",
           "sys_vendor": "Gigabyte"}
    v = dmi_bios.classify(dmi, catalog=catalog)
    assert v["verdict"] == "outdated"
    assert "F11" in v["reason"]
    assert "F16" in v["reason"]
    assert "rebar" in v["reason"].lower()
    assert "https://example.com/x570" in v["recommendation"]


def test_classify_up_to_date_when_meets_rebar():
    catalog = {
        "X570 AORUS ELITE": {
            "min_rebar": "F16",
            "min_aer": "F15",
            "vendor_url": "https://example.com/x570",
        },
    }
    dmi = {"bios_version": "F16",
           "board_name": "X570 AORUS ELITE",
           "sys_vendor": "Gigabyte"}
    v = dmi_bios.classify(dmi, catalog=catalog)
    assert v["verdict"] == "up_to_date"


def test_classify_up_to_date_when_exceeds_rebar():
    catalog = {
        "X570 AORUS ELITE": {
            "min_rebar": "F16",
            "min_aer": "F15",
            "vendor_url": "https://example.com/x570",
        },
    }
    dmi = {"bios_version": "F22",
           "board_name": "X570 AORUS ELITE",
           "sys_vendor": "Gigabyte"}
    v = dmi_bios.classify(dmi, catalog=catalog)
    assert v["verdict"] == "up_to_date"


def test_classify_outdated_for_aer_when_rebar_met():
    catalog = {
        "B550 PRO": {
            "min_rebar": "1.0",
            "min_aer": "1.5",
            "vendor_url": "https://example.com/b550",
        },
    }
    dmi = {"bios_version": "1.2",
           "board_name": "B550 PRO",
           "sys_vendor": "ASRock"}
    v = dmi_bios.classify(dmi, catalog=catalog)
    assert v["verdict"] == "outdated"
    assert "aer" in v["reason"].lower()


def test_compare_versions_numeric_dotted():
    assert dmi_bios.version_ge("1.5", "1.2") is True
    assert dmi_bios.version_ge("1.2", "1.5") is False
    assert dmi_bios.version_ge("2.0", "1.99") is True
    assert dmi_bios.version_ge("1.5", "1.5") is True


def test_compare_versions_gigabyte_style():
    # F11 < F16 lexicographic-numeric
    assert dmi_bios.version_ge("F16", "F11") is True
    assert dmi_bios.version_ge("F11", "F16") is False
    assert dmi_bios.version_ge("F16", "F16") is True


def test_compare_versions_none_returns_false():
    assert dmi_bios.version_ge(None, "F11") is False
    assert dmi_bios.version_ge("F16", None) is False


def test_status_returns_full_payload(fake_dmi, tmp_path, monkeypatch):
    _write(fake_dmi,
           bios_version="F11",
           bios_date="04/05/2023",
           board_name="X570 AORUS ELITE",
           sys_vendor="Gigabyte")
    bp = tmp_path / "baseline.json"
    monkeypatch.setattr(dmi_bios, "_DMI_ROOT", str(fake_dmi))
    monkeypatch.setattr(dmi_bios, "baseline_path", lambda: str(bp))
    catalog = {
        "X570 AORUS ELITE": {
            "min_rebar": "F16", "min_aer": "F15",
            "vendor_url": "https://gigabyte.com/x570",
        },
    }
    monkeypatch.setattr(dmi_bios, "_CATALOG", catalog)
    s = dmi_bios.status()
    assert s["ok"] is True
    assert s["dmi"]["bios_version"] == "F11"
    assert s["dmi"]["bios_date"] == "04/05/2023"
    assert s["bios_date_iso"] == "2023-04-05"
    assert s["verdict"]["verdict"] == "outdated"
    assert s["drift"]["status"] == "baseline_recorded"


def test_status_detects_no_drift_on_repeat(fake_dmi, tmp_path, monkeypatch):
    _write(fake_dmi, bios_version="F16", bios_date="09/15/2024",
           board_name="B1", sys_vendor="X")
    bp = tmp_path / "b.json"
    monkeypatch.setattr(dmi_bios, "_DMI_ROOT", str(fake_dmi))
    monkeypatch.setattr(dmi_bios, "baseline_path", lambda: str(bp))
    monkeypatch.setattr(dmi_bios, "_CATALOG", {})
    dmi_bios.status()  # writes baseline
    s2 = dmi_bios.status()
    assert s2["drift"]["status"] == "no_drift"


def test_status_detects_drift_on_bios_change(fake_dmi, tmp_path, monkeypatch):
    _write(fake_dmi, bios_version="F11", bios_date="04/05/2023",
           board_name="B1", sys_vendor="X")
    bp = tmp_path / "b.json"
    monkeypatch.setattr(dmi_bios, "_DMI_ROOT", str(fake_dmi))
    monkeypatch.setattr(dmi_bios, "baseline_path", lambda: str(bp))
    monkeypatch.setattr(dmi_bios, "_CATALOG", {})
    dmi_bios.status()
    # User flashes BIOS
    (fake_dmi / "bios_version").write_text("F16")
    (fake_dmi / "bios_date").write_text("09/15/2024")
    s2 = dmi_bios.status()
    assert s2["drift"]["status"] == "drift_detected"
    assert s2["drift"]["from"]["bios_version"] == "F11"
    assert s2["drift"]["to"]["bios_version"] == "F16"


def test_status_handles_missing_dmi_root(tmp_path, monkeypatch):
    bp = tmp_path / "b.json"
    monkeypatch.setattr(dmi_bios, "_DMI_ROOT", str(tmp_path / "nope"))
    monkeypatch.setattr(dmi_bios, "baseline_path", lambda: str(bp))
    monkeypatch.setattr(dmi_bios, "_CATALOG", {})
    s = dmi_bios.status()
    assert s["ok"] is False
    assert s["error"] == "dmi_unavailable"


def test_status_partial_dmi_fields_still_works(fake_dmi, tmp_path, monkeypatch):
    # Real-world: QEMU VM has no board_name
    _write(fake_dmi, bios_version="4.2025.02-4~bpo12+1",
           bios_date="07/10/2025", sys_vendor="QEMU",
           product_name="Standard PC (Q35 + ICH9, 2009)")
    bp = tmp_path / "b.json"
    monkeypatch.setattr(dmi_bios, "_DMI_ROOT", str(fake_dmi))
    monkeypatch.setattr(dmi_bios, "baseline_path", lambda: str(bp))
    monkeypatch.setattr(dmi_bios, "_CATALOG", {})
    s = dmi_bios.status()
    assert s["ok"] is True
    assert s["dmi"]["board_name"] is None
    assert s["dmi"]["sys_vendor"] == "QEMU"
    assert s["verdict"]["verdict"] == "unknown"


def test_catalog_is_loaded_from_module():
    # Catalog should exist as a dict at module level
    assert hasattr(dmi_bios, "_CATALOG")
    assert isinstance(dmi_bios._CATALOG, dict)


def test_baseline_path_under_config_dir(monkeypatch):
    monkeypatch.delenv("HOME", raising=False)
    monkeypatch.setenv("HOME", "/tmp/fake_home_test")
    assert dmi_bios.baseline_path().endswith(
        "/.config/gpu-dashboard/dmi_baseline.json"
    )
