"""Tests for modules/pci_sriov_posture_audit.py — R&D #77.4."""
from __future__ import annotations

import pytest

from gpu_dashboard.modules import pci_sriov_posture_audit as mod


def _mk_pci(root, bdf, **knobs):
    d = root / bdf
    d.mkdir(parents=True, exist_ok=True)
    for k, v in knobs.items():
        if v is not None:
            (d / k).write_text(f"{v}\n")


# --- list_sriov_devices ----------------------------------------

def test_list_missing(tmp_path):
    assert mod.list_sriov_devices(str(tmp_path / "nope")) == []


def test_list_skips_non_sriov(tmp_path):
    _mk_pci(tmp_path, "0000:01:00.0")  # no sriov_totalvfs
    assert mod.list_sriov_devices(str(tmp_path)) == []


def test_list_one_sriov(tmp_path):
    _mk_pci(tmp_path, "0000:01:00.0",
                sriov_totalvfs=8, sriov_numvfs=0,
                sriov_drivers_autoprobe=1)
    out = mod.list_sriov_devices(str(tmp_path))
    assert len(out) == 1
    assert out[0]["bdf"] == "0000:01:00.0"
    assert out[0]["sriov_totalvfs"] == 8
    assert out[0]["sriov_numvfs"] == 0


# --- classify ---------------------------------------------------

def test_classify_unknown():
    v = mod.classify(False, [], False)
    assert v["verdict"] == "unknown"


def test_classify_no_sriov_capable():
    v = mod.classify(True, [], False)
    assert v["verdict"] == "no_sriov_capable"


def test_classify_unexpected_vfs_active():
    v = mod.classify(
        True,
        [{"bdf": "0000:01:00.0",
            "sriov_totalvfs": 8, "sriov_numvfs": 4,
            "sriov_drivers_autoprobe": 1,
            "sriov_offset": 1, "sriov_stride": 1,
            "sriov_vf_total_msix": 256}],
        False)
    assert v["verdict"] == "unexpected_vfs_active"


def test_classify_autoprobe_off_no_vfio():
    v = mod.classify(
        True,
        [{"bdf": "0000:01:00.0",
            "sriov_totalvfs": 8, "sriov_numvfs": 0,
            "sriov_drivers_autoprobe": 0,
            "sriov_offset": 1, "sriov_stride": 1,
            "sriov_vf_total_msix": 256}],
        False)
    assert v["verdict"] == \
        "drivers_autoprobe_disabled_no_vfio"


def test_classify_autoprobe_off_with_vfio_ok():
    # autoprobe off WITH vfio loaded = expected setup
    v = mod.classify(
        True,
        [{"bdf": "0000:01:00.0",
            "sriov_totalvfs": 8, "sriov_numvfs": 0,
            "sriov_drivers_autoprobe": 0,
            "sriov_offset": 1, "sriov_stride": 1,
            "sriov_vf_total_msix": 256}],
        True)
    assert v["verdict"] == "sriov_capable_unused"


def test_classify_sriov_capable_unused():
    v = mod.classify(
        True,
        [{"bdf": "0000:01:00.0",
            "sriov_totalvfs": 8, "sriov_numvfs": 0,
            "sriov_drivers_autoprobe": 1,
            "sriov_offset": 1, "sriov_stride": 1,
            "sriov_vf_total_msix": 256}],
        False)
    assert v["verdict"] == "sriov_capable_unused"


# Priority : active > autoprobe > unused
def test_priority_active_over_autoprobe():
    v = mod.classify(
        True,
        [{"bdf": "0000:01:00.0",
            "sriov_totalvfs": 8, "sriov_numvfs": 4,
            "sriov_drivers_autoprobe": 0,
            "sriov_offset": 1, "sriov_stride": 1,
            "sriov_vf_total_msix": 256}],
        False)
    assert v["verdict"] == "unexpected_vfs_active"


# --- status integration ----------------------------------------

def test_status_unknown(tmp_path):
    out = mod.status(None,
                          str(tmp_path / "nope"),
                          str(tmp_path / "no_vfio"))
    assert out["ok"] is False
    assert out["verdict"]["verdict"] == "unknown"


def test_status_no_sriov(tmp_path):
    pci = tmp_path / "pci"; pci.mkdir()
    _mk_pci(pci, "0000:01:00.0")  # no sriov files
    out = mod.status(None, str(pci),
                          str(tmp_path / "no_vfio"))
    assert out["ok"] is True
    assert out["verdict"]["verdict"] == "no_sriov_capable"


def test_status_active_vfs(tmp_path):
    pci = tmp_path / "pci"; pci.mkdir()
    _mk_pci(pci, "0000:01:00.0",
                sriov_totalvfs=8, sriov_numvfs=4,
                sriov_drivers_autoprobe=1)
    out = mod.status(None, str(pci),
                          str(tmp_path / "no_vfio"))
    assert out["sriov_capable_count"] == 1
    assert out["active_vf_count"] == 4
    assert out["verdict"]["verdict"] == "unexpected_vfs_active"
