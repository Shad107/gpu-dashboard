"""Tests for modules/spi_firmware_loader_audit.py — R&D #66.4."""
from __future__ import annotations

import os

import pytest

from gpu_dashboard.modules import spi_firmware_loader_audit as mod


def _mk_spi(root, name):
    d = root / "spi_master" / name
    d.mkdir(parents=True, exist_ok=True)


def _mk_fw(root, name, *, loading=0):
    d = root / "firmware" / name
    d.mkdir(parents=True, exist_ok=True)
    (d / "loading").write_text(f"{loading}\n")


def _mk_timeout(root, value=60):
    p = root / "firmware" / "timeout"
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(f"{value}\n")


def _mk_profiling(root, value=0):
    p = root / "profiling"
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(f"{value}\n")


# --- list_spi_masters -------------------------------------------

def test_list_spi_masters_missing(tmp_path):
    assert mod.list_spi_masters(str(tmp_path / "nope")) == []


def test_list_spi_masters(tmp_path):
    _mk_spi(tmp_path, "spi0")
    _mk_spi(tmp_path, "spi1")
    out = mod.list_spi_masters(str(tmp_path / "spi_master"))
    ids = [e["id"] for e in out]
    assert ids == ["spi0", "spi1"]


# --- list_firmware_requests ------------------------------------

def test_list_firmware_requests_missing(tmp_path):
    assert mod.list_firmware_requests(str(tmp_path / "nope")) == []


def test_list_firmware_requests_skips_timeout(tmp_path):
    # Live host : /sys/class/firmware contains a "timeout" file.
    _mk_timeout(tmp_path, 60)
    out = mod.list_firmware_requests(str(tmp_path / "firmware"))
    assert out == []


def test_list_firmware_requests(tmp_path):
    _mk_fw(tmp_path, "iwlwifi-9000.ucode", loading=1)
    _mk_fw(tmp_path, "amdgpu_raven_vcn.bin", loading=0)
    out = mod.list_firmware_requests(str(tmp_path / "firmware"))
    names = sorted(r["name"] for r in out)
    assert names == ["amdgpu_raven_vcn.bin",
                       "iwlwifi-9000.ucode"]
    loadings = {r["name"]: r["loading"] for r in out}
    assert loadings["iwlwifi-9000.ucode"] == 1


# --- classify ---------------------------------------------------

def test_classify_unknown():
    v = mod.classify([], [], None, False, False, False)
    assert v["verdict"] == "unknown"


def test_classify_firmware_stuck():
    v = mod.classify([], [{"name": "iwlwifi.ucode", "loading": 1}],
                          0, True, True, True)
    assert v["verdict"] == "firmware_load_stuck"
    assert "iwlwifi" in v["reason"]
    assert "linux-firmware" in v["recommendation"]


def test_classify_profiling_enabled():
    v = mod.classify([{"id": "spi0"}], [], 1,
                          True, True, True)
    assert v["verdict"] == "profiling_enabled"
    assert "profiling" in v["recommendation"]


def test_classify_spi_no_master():
    v = mod.classify([], [], 0, True, True, True)
    assert v["verdict"] == "spi_no_master"


def test_classify_ok():
    v = mod.classify([{"id": "spi0"}],
                          [{"name": "fw.bin", "loading": 0}],
                          0, True, True, True)
    assert v["verdict"] == "ok"


# Firmware-stuck must win over profiling and spi_no_master.
def test_priority_firmware_over_profiling():
    v = mod.classify([],
                          [{"name": "x", "loading": 1}],
                          1,
                          True, True, True)
    assert v["verdict"] == "firmware_load_stuck"


# --- status integration -----------------------------------------

def test_status_unknown(tmp_path):
    sp = str(tmp_path / "nospi")
    fw = str(tmp_path / "nofw")
    pf = str(tmp_path / "noprof")
    out = mod.status(None, sp, fw, pf)
    assert out["ok"] is False
    assert out["verdict"]["verdict"] == "unknown"


def test_status_live_like(tmp_path):
    """Mirror the live VM: spi_master empty, firmware/timeout
    only, profiling=0."""
    (tmp_path / "spi_master").mkdir()
    _mk_timeout(tmp_path, 60)
    _mk_profiling(tmp_path, 0)
    out = mod.status(None,
                          str(tmp_path / "spi_master"),
                          str(tmp_path / "firmware"),
                          str(tmp_path / "profiling"))
    assert out["ok"] is True
    assert out["spi_master_count"] == 0
    assert out["firmware_request_count"] == 0
    assert out["profiling"] == 0
    # spi_master exists but empty → spi_no_master
    assert out["verdict"]["verdict"] == "spi_no_master"
