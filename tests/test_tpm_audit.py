"""Tests for modules/tpm_audit.py — R&D #49.2."""
from __future__ import annotations

import pytest

from gpu_dashboard.modules import tpm_audit as mod


def _mk_tpm(root, name="tpm0", version=2, locality=0):
    d = root / name
    d.mkdir(parents=True, exist_ok=True)
    (d / "tpm_version_major").write_text(str(version) + "\n")
    (d / "active_locality").write_text(str(locality) + "\n")


def _mk_measurements(root, size_bytes=1024):
    d = root
    d.mkdir(parents=True, exist_ok=True)
    (d / "binary_bios_measurements").write_bytes(b"\0" * size_bytes)


# --- list_tpms ----------------------------------------------------

def test_list_tpms_basic(tmp_path):
    _mk_tpm(tmp_path, "tpm0", version=2)
    out = mod.list_tpms(str(tmp_path))
    assert len(out) == 1
    assert out[0]["tpm_version_major"] == 2


def test_list_tpms_missing(tmp_path):
    assert mod.list_tpms(str(tmp_path / "nope")) == []


def test_list_tpms_empty(tmp_path):
    assert mod.list_tpms(str(tmp_path)) == []


# --- measured_boot_present ----------------------------------------

def test_measured_boot_missing(tmp_path):
    out = mod.measured_boot_present(str(tmp_path / "nope"))
    assert out["available"] is False


def test_measured_boot_present_readable(tmp_path):
    _mk_measurements(tmp_path, size_bytes=1024)
    out = mod.measured_boot_present(str(tmp_path))
    assert out["available"] is True
    assert out["size_bytes"] == 1024


# --- classify -----------------------------------------------------

def _tpm(version=2, name="tpm0"):
    return {"name": name, "tpm_version_major": version,
              "active_locality": 0, "firmware_path": None,
              "vendor_id_str": None}


def test_classify_no_tpm():
    v = mod.classify([], {"available": False})
    assert v["verdict"] == "no_tpm"


def test_classify_tpm1_legacy():
    v = mod.classify([_tpm(version=1)],
                       {"available": True, "size_bytes": 1024})
    assert v["verdict"] == "tpm1_legacy"


def test_classify_measured_boot_missing():
    v = mod.classify([_tpm(version=2)],
                       {"available": False, "size_bytes": 0})
    assert v["verdict"] == "measured_boot_missing"


def test_classify_ok():
    v = mod.classify([_tpm(version=2)],
                       {"available": True, "size_bytes": 1024})
    assert v["verdict"] == "ok"


def test_classify_priority_legacy_wins():
    v = mod.classify([_tpm(version=1)],
                       {"available": False})
    assert v["verdict"] == "tpm1_legacy"


# --- status integration -------------------------------------------

def test_status_no_tpm(monkeypatch, tmp_path):
    monkeypatch.setattr(mod, "_SYS_CLASS_TPM",
                        str(tmp_path / "nope"))
    monkeypatch.setattr(mod, "_SECURITY_TPM",
                        str(tmp_path / "nope2"))
    out = mod.status()
    assert out["ok"] is False
    assert out["verdict"]["verdict"] == "unknown"


def test_status_empty_tpm_dir(monkeypatch, tmp_path):
    tpm_dir = tmp_path / "tpm"
    tpm_dir.mkdir()
    monkeypatch.setattr(mod, "_SYS_CLASS_TPM", str(tpm_dir))
    monkeypatch.setattr(mod, "_SECURITY_TPM",
                        str(tmp_path / "sec"))
    out = mod.status()
    assert out["ok"] is True
    assert out["verdict"]["verdict"] == "no_tpm"


def test_status_with_tpm2(monkeypatch, tmp_path):
    tpm_dir = tmp_path / "tpm"
    tpm_dir.mkdir()
    _mk_tpm(tpm_dir, "tpm0", version=2)
    sec_dir = tmp_path / "sec"
    _mk_measurements(sec_dir, size_bytes=2048)
    monkeypatch.setattr(mod, "_SYS_CLASS_TPM", str(tpm_dir))
    monkeypatch.setattr(mod, "_SECURITY_TPM", str(sec_dir))
    out = mod.status()
    assert out["tpm_count"] == 1
    assert out["measured_boot"]["size_bytes"] == 2048
    assert out["verdict"]["verdict"] == "ok"
