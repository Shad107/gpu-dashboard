"""Tests for modules/nvidia_rm_audit.py — R&D #47.3."""
from __future__ import annotations

import pytest

from gpu_dashboard.modules import nvidia_rm_audit as mod


VERSION_SAMPLE = (
    "NVRM version: NVIDIA UNIX Open Kernel Module for x86_64  "
    "590.48.01  Release Build  (dvs-builder@U22-I3-AE18-23-3)  "
    "Mon Dec  8 13:05:00 UTC 2025\n"
    "GCC version:  gcc version 15.2.0 (Ubuntu 15.2.0-4ubuntu4)\n"
)

PARAMS_SAMPLE = (
    "ResmanDebugLevel: 4294967295\n"
    "RmLogonRC: 1\n"
    "ModifyDeviceFiles: 1\n"
    "DeviceFileUID: 0\n"
    "DeviceFileGID: 0\n"
)


# --- parse_params ------------------------------------------------

def test_parse_params_basic():
    out = mod.parse_params(PARAMS_SAMPLE)
    assert out["ResmanDebugLevel"] == "4294967295"
    assert out["RmLogonRC"] == "1"


def test_parse_params_empty():
    assert mod.parse_params("") == {}


def test_parse_params_skips_no_colon():
    out = mod.parse_params("no colon here\nKey: Val\n")
    assert out == {"Key": "Val"}


# --- parse_version ----------------------------------------------

def test_parse_version_basic():
    assert mod.parse_version(VERSION_SAMPLE) == "590.48.01"


def test_parse_version_two_digit():
    v = "NVRM version: NVIDIA UNIX Open Kernel Module 535.171\n"
    assert mod.parse_version(v) == "535.171"


def test_parse_version_none():
    assert mod.parse_version("") is None
    assert mod.parse_version(None) is None
    assert mod.parse_version("garbage line") is None


# --- list_capabilities ------------------------------------------

def test_list_capabilities_walk(tmp_path):
    caps = tmp_path / "capabilities"
    (caps / "mig").mkdir(parents=True)
    (caps / "mig" / "config").write_text("")
    (caps / "mig" / "monitor").write_text("")
    (caps / "fabric-imex-mgmt").write_text("")
    out = mod.list_capabilities(str(tmp_path))
    assert "fabric-imex-mgmt" in out
    assert "mig/config" in out
    assert "mig/monitor" in out


def test_list_capabilities_missing(tmp_path):
    assert mod.list_capabilities(str(tmp_path / "nope")) == []


# --- classify ----------------------------------------------------

def test_classify_no_driver():
    v = mod.classify(None, None, {}, [], driver_present=False)
    assert v["verdict"] == "no_nvidia_driver"


def test_classify_ok():
    v = mod.classify("590.48.01", None, {"RmLogonRC": "1"},
                       ["mig/config", "mig/monitor",
                        "fabric-imex-mgmt"],
                       driver_present=True)
    assert v["verdict"] == "ok"


def test_classify_caps_missing():
    v = mod.classify("590.48.01", None, {"RmLogonRC": "1"},
                       ["fabric-imex-mgmt"],  # mig/* missing
                       driver_present=True)
    assert v["verdict"] == "caps_missing"
    assert "mig/config" in v["reason"]


def test_classify_kmod_mismatch():
    v = mod.classify("550.135", "555.42.06", {}, [], driver_present=True)
    assert v["verdict"] == "driver_kmod_mismatch"
    assert "550" in v["reason"]
    assert "555" in v["reason"]


def test_classify_unknown_when_present_but_empty():
    v = mod.classify(None, None, {}, [], driver_present=True)
    assert v["verdict"] == "unknown"


def test_classify_mismatch_wins_over_caps_missing():
    v = mod.classify("550", "555", {}, [], driver_present=True)
    assert v["verdict"] == "driver_kmod_mismatch"


# --- status integration -----------------------------------------

def test_status_no_driver(monkeypatch, tmp_path):
    monkeypatch.setattr(mod, "_PROC_NVIDIA", str(tmp_path / "nope"))
    out = mod.status()
    assert out["ok"] is False
    assert out["verdict"]["verdict"] == "no_nvidia_driver"


def test_status_with_driver(monkeypatch, tmp_path):
    nv = tmp_path / "nv"
    nv.mkdir()
    (nv / "version").write_text(VERSION_SAMPLE)
    (nv / "params").write_text(PARAMS_SAMPLE)
    caps = nv / "capabilities"
    (caps / "mig").mkdir(parents=True)
    (caps / "mig" / "config").write_text("")
    (caps / "mig" / "monitor").write_text("")
    (caps / "fabric-imex-mgmt").write_text("")
    monkeypatch.setattr(mod, "_PROC_NVIDIA", str(nv))
    monkeypatch.setattr(mod, "_smi_version", lambda cfg: None)
    out = mod.status()
    assert out["ok"] is True
    assert out["version_proc"] == "590.48.01"
    assert out["capability_count"] == 3
    assert out["verdict"]["verdict"] == "ok"


def test_status_caps_missing(monkeypatch, tmp_path):
    nv = tmp_path / "nv"
    nv.mkdir()
    (nv / "version").write_text(VERSION_SAMPLE)
    (nv / "params").write_text(PARAMS_SAMPLE)
    (nv / "capabilities").mkdir()
    # No mig/* — caps_missing should fire.
    monkeypatch.setattr(mod, "_PROC_NVIDIA", str(nv))
    monkeypatch.setattr(mod, "_smi_version", lambda cfg: None)
    out = mod.status()
    assert out["verdict"]["verdict"] == "caps_missing"
