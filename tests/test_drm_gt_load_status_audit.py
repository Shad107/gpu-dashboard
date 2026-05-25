"""Tests for modules/drm_gt_load_status_audit.py R&D #105.2."""
from __future__ import annotations

import pytest

from gpu_dashboard.modules import drm_gt_load_status_audit as mod


# --- find_drm_cards --------------------------------------------

def test_find_cards_missing(tmp_path):
    assert mod.find_drm_cards(
        str(tmp_path / "nope")) == []


def test_find_cards_intel(tmp_path):
    d = tmp_path / "drm" / "card0" / "device"
    d.mkdir(parents=True)
    (d / "vendor").write_text("0x8086\n")
    out = mod.find_drm_cards(str(tmp_path / "drm"))
    assert len(out) == 1
    assert out[0]["vendor"] == "intel"


def test_find_cards_amd(tmp_path):
    d = tmp_path / "drm" / "card0" / "device"
    d.mkdir(parents=True)
    (d / "vendor").write_text("0x1002\n")
    out = mod.find_drm_cards(str(tmp_path / "drm"))
    assert out[0]["vendor"] == "amd"


def test_find_cards_nvidia(tmp_path):
    d = tmp_path / "drm" / "card0" / "device"
    d.mkdir(parents=True)
    (d / "vendor").write_text("0x10de\n")
    out = mod.find_drm_cards(str(tmp_path / "drm"))
    assert out[0]["vendor"] == "other"


# --- parse_uc_info ---------------------------------------------

def test_parse_uc_loaded():
    text = (
        "GuC firmware: i915/tgl_guc_70.bin\n"
        "version: 70.13\n"
        "status: RUNNING\n")
    out = mod.parse_uc_info(text)
    assert out["loaded"] is True
    assert out["raw_status"] == "RUNNING"
    assert out["version"] == "70.13"


def test_parse_uc_failed():
    text = (
        "fw status: TRANSFERRED\n"
        "Init Error: -ENOEXEC\n")
    out = mod.parse_uc_info(text)
    assert out["loaded"] is False
    assert out["raw_status"] == "TRANSFERRED"


def test_parse_uc_empty():
    assert mod.parse_uc_info("")["loaded"] is None
    assert mod.parse_uc_info(None)["loaded"] is None


# --- classify --------------------------------------------------

def test_classify_unknown_no_gt():
    v = mod.classify([], False, False, True, {}, {}, None)
    assert v["verdict"] == "unknown"


def test_classify_unknown_nvidia_only():
    cards = [{"card": "card0", "vendor": "other",
              "vendor_id": "0x10de", "device_path": "/x"}]
    v = mod.classify(cards, False, False, True,
                          {}, {}, None)
    assert v["verdict"] == "unknown"


def test_classify_ok_intel():
    cards = [{"card": "card0", "vendor": "intel",
              "vendor_id": "0x8086", "device_path": "/x"}]
    v = mod.classify(cards, True, False, True,
                          {"loaded": True,
                           "raw_status": "RUNNING"},
                          {"loaded": True,
                           "raw_status": "RUNNING"},
                          None)
    assert v["verdict"] == "ok"


def test_classify_guc_not_loaded():
    cards = [{"card": "card0", "vendor": "intel",
              "vendor_id": "0x8086", "device_path": "/x"}]
    v = mod.classify(cards, True, False, True,
                          {"loaded": False,
                           "raw_status": "TRANSFERRED"},
                          {"loaded": True,
                           "raw_status": "RUNNING"},
                          None)
    assert v["verdict"] == "guc_not_loaded"


def test_classify_huc_failed():
    cards = [{"card": "card0", "vendor": "intel",
              "vendor_id": "0x8086", "device_path": "/x"}]
    v = mod.classify(cards, True, False, True,
                          {"loaded": True,
                           "raw_status": "RUNNING"},
                          {"loaded": False,
                           "raw_status": "ERROR"},
                          None)
    assert v["verdict"] == "huc_load_failed"


def test_classify_amdgpu_recovery_off():
    cards = [{"card": "card0", "vendor": "amd",
              "vendor_id": "0x1002", "device_path": "/x"}]
    v = mod.classify(cards, False, True, True,
                          {}, {}, 0)
    assert v["verdict"] == "amdgpu_recovery_off"


def test_classify_requires_root_intel_no_debugfs():
    cards = [{"card": "card0", "vendor": "intel",
              "vendor_id": "0x8086", "device_path": "/x"}]
    v = mod.classify(cards, True, False, False,
                          {}, {}, None)
    assert v["verdict"] == "requires_root"


# --- status integration ----------------------------------------

def test_status_unknown(tmp_path):
    out = mod.status(None,
                       str(tmp_path / "no_drm"),
                       str(tmp_path / "no_dri"))
    assert out["verdict"]["verdict"] == "unknown"


def test_status_nvidia_only(tmp_path):
    d = tmp_path / "drm" / "card0" / "device"
    d.mkdir(parents=True)
    (d / "vendor").write_text("0x10de\n")
    out = mod.status(None, str(tmp_path / "drm"),
                       str(tmp_path / "no_dri"))
    assert out["verdict"]["verdict"] == "unknown"
    assert out["intel_present"] is False
    assert out["amd_present"] is False
