"""Tests for modules/nvmem_inventory_audit.py — R&D #69.1."""
from __future__ import annotations

import os

import pytest

from gpu_dashboard.modules import nvmem_inventory_audit as mod


def _mk_device(root, name, *, type_="EEPROM",
                  nvmem_size=256, nvmem_mode=0o400,
                  force_ro="1"):
    d = root / name
    d.mkdir(parents=True, exist_ok=True)
    (d / "type").write_text(type_ + "\n")
    (d / "force_ro").write_text(force_ro + "\n")
    nv = d / "nvmem"
    nv.write_bytes(b"x" * nvmem_size)
    os.chmod(str(nv), nvmem_mode)


# --- list_nvmem_devices ----------------------------------------

def test_list_devices_missing(tmp_path):
    assert mod.list_nvmem_devices(str(tmp_path / "nope")) == []


def test_list_devices_one(tmp_path):
    _mk_device(tmp_path, "cmos_nvram0",
                  type_="Unknown",
                  nvmem_size=242, nvmem_mode=0o644)
    out = mod.list_nvmem_devices(str(tmp_path))
    assert len(out) == 1
    assert out[0]["id"] == "cmos_nvram0"
    assert out[0]["type"] == "Unknown"
    assert out[0]["nvmem_size"] == 242
    assert out[0]["nvmem_mode"] == 0o644


# --- _is_secret_provider ---------------------------------------

def test_secret_provider_otp():
    assert mod._is_secret_provider(
        {"id": "qfprom_otp0", "type": "OTP"}) is True


def test_secret_provider_tpm():
    assert mod._is_secret_provider(
        {"id": "tpm_nvram0", "type": ""}) is True


def test_secret_provider_cmos_no():
    assert mod._is_secret_provider(
        {"id": "cmos_nvram0", "type": "Unknown"}) is False


def test_secret_provider_eeprom_no():
    assert mod._is_secret_provider(
        {"id": "0-0050", "type": "EEPROM"}) is False


# --- classify ---------------------------------------------------

def test_classify_unknown():
    v = mod.classify([], False, False)
    assert v["verdict"] == "unknown"


def test_classify_requires_root():
    v = mod.classify([], True, False)
    assert v["verdict"] == "requires_root"


def test_classify_ok():
    v = mod.classify(
        [{"id": "0-0050", "type": "EEPROM",
            "nvmem_size": 256, "nvmem_mode": 0o400,
            "force_ro": "1"}],
        True, True)
    assert v["verdict"] == "ok"


def test_classify_writable():
    v = mod.classify(
        [{"id": "0-0050", "type": "EEPROM",
            "nvmem_size": 256, "nvmem_mode": 0o666,
            "force_ro": "0"}],
        True, True)
    assert v["verdict"] == "writable_nvmem"


def test_classify_world_readable_secret():
    v = mod.classify(
        [{"id": "qfprom_otp0", "type": "OTP",
            "nvmem_size": 256, "nvmem_mode": 0o644,
            "force_ro": "1"}],
        True, True)
    assert v["verdict"] == "world_readable_secret_nvmem"


def test_classify_world_readable_non_secret_ok():
    # CMOS RAM world-readable is fine.
    v = mod.classify(
        [{"id": "cmos_nvram0", "type": "Unknown",
            "nvmem_size": 242, "nvmem_mode": 0o644,
            "force_ro": "0"}],
        True, True)
    # Unknown provider triggers stale verdict, not secret.
    assert v["verdict"] == "stale_or_unknown_provider"


def test_classify_stale_or_unknown_provider():
    v = mod.classify(
        [{"id": "cmos_nvram0", "type": "Unknown",
            "nvmem_size": 242, "nvmem_mode": 0o400,
            "force_ro": "0"}],
        True, True)
    assert v["verdict"] == "stale_or_unknown_provider"


# Priority : writable > secret_readable > stale > requires_root
def test_priority_writable_over_secret():
    v = mod.classify(
        [{"id": "qfprom_otp0", "type": "OTP",
            "nvmem_size": 256, "nvmem_mode": 0o666,
            "force_ro": "0"}],
        True, True)
    assert v["verdict"] == "writable_nvmem"


def test_priority_secret_over_stale():
    v = mod.classify(
        [{"id": "tpm_nvram", "type": "Unknown",
            "nvmem_size": 256, "nvmem_mode": 0o644,
            "force_ro": "1"}],
        True, True)
    assert v["verdict"] == "world_readable_secret_nvmem"


# --- status integration -----------------------------------------

def test_status_unknown(tmp_path):
    out = mod.status(None, str(tmp_path / "nope"))
    assert out["ok"] is False
    assert out["verdict"]["verdict"] == "unknown"


def test_status_synthetic(tmp_path):
    _mk_device(tmp_path, "cmos_nvram0",
                  type_="Unknown", nvmem_size=242,
                  nvmem_mode=0o644)
    out = mod.status(None, str(tmp_path))
    assert out["ok"] is True
    assert out["device_count"] == 1
    # Unknown provider → stale verdict (informational).
    assert out["verdict"]["verdict"] == "stale_or_unknown_provider"


def test_status_secret_world_readable(tmp_path):
    _mk_device(tmp_path, "qfprom_otp0",
                  type_="OTP", nvmem_size=4096,
                  nvmem_mode=0o644)
    out = mod.status(None, str(tmp_path))
    assert out["verdict"]["verdict"] == "world_readable_secret_nvmem"
