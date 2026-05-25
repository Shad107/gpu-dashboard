"""Tests for modules/acpi_boot_assets_audit.py R&D #109.2."""
from __future__ import annotations

import pytest

from gpu_dashboard.modules import acpi_boot_assets_audit as mod


def test_classify_unknown():
    v = mod.classify(False, False, None, None, False)
    assert v["verdict"] == "unknown"


def test_classify_requires_root():
    v = mod.classify(True, True, None, None, False)
    assert v["verdict"] == "requires_root"


def test_classify_ok_bgrt_valid():
    v = mod.classify(True, True, 1, 0, False)
    assert v["verdict"] == "ok"


def test_classify_bgrt_status_invalid():
    # status & 1 == 0 → invalid
    v = mod.classify(True, True, 0, 0, False)
    assert v["verdict"] == "bgrt_status_invalid"


def test_classify_bgrt_status_2_invalid():
    # status=2 (bit 0 still clear) → invalid
    v = mod.classify(True, True, 2, 0, False)
    assert v["verdict"] == "bgrt_status_invalid"


def test_classify_no_assets_accent():
    v = mod.classify(True, False, None, None, False)
    assert v["verdict"] == "no_boot_assets"


def test_classify_ok_only_fpdt():
    v = mod.classify(True, False, None, None, True)
    assert v["verdict"] == "ok"


def test_status_unknown(tmp_path):
    out = mod.status(None, str(tmp_path / "nope"))
    assert out["verdict"]["verdict"] == "unknown"


def test_status_ok_bgrt(tmp_path):
    d = tmp_path / "acpi" / "bgrt"
    d.mkdir(parents=True)
    (d / "status").write_text("1\n")
    (d / "type").write_text("0\n")
    out = mod.status(None, str(tmp_path / "acpi"))
    assert out["verdict"]["verdict"] == "ok"
    assert out["bgrt_present"] is True


def test_status_bgrt_invalid(tmp_path):
    d = tmp_path / "acpi" / "bgrt"
    d.mkdir(parents=True)
    (d / "status").write_text("0\n")
    (d / "type").write_text("0\n")
    out = mod.status(None, str(tmp_path / "acpi"))
    assert (out["verdict"]["verdict"]
            == "bgrt_status_invalid")
