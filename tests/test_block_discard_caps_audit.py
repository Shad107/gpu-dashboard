"""Tests for modules/block_discard_caps_audit.py — R&D #96.1."""
from __future__ import annotations

import pytest

from gpu_dashboard.modules import block_discard_caps_audit as mod


def _mk_blockdev(tmp_path, name, *, discard_max="1073741824",
                  discard_gran="4096", rotational="0",
                  provisioning_mode=None):
    d = tmp_path / "block" / name
    q = d / "queue"
    q.mkdir(parents=True, exist_ok=True)
    if discard_max is not None:
        (q / "discard_max_bytes").write_text(
            discard_max + "\n")
    (q / "discard_granularity").write_text(
        discard_gran + "\n")
    (q / "rotational").write_text(rotational + "\n")
    if provisioning_mode is not None:
        scsi = d / "device" / "scsi_disk" / "0:0:0:0"
        scsi.mkdir(parents=True, exist_ok=True)
        (scsi / "provisioning_mode").write_text(
            provisioning_mode + "\n")
    return str(tmp_path / "block")


# --- walk_block_devs -------------------------------------------

def test_walk_missing(tmp_path):
    assert mod.walk_block_devs(str(tmp_path / "nope")) == []


def test_walk_skips_virtual(tmp_path):
    _mk_blockdev(tmp_path, "loop0")
    _mk_blockdev(tmp_path, "ram0")
    _mk_blockdev(tmp_path, "zram0")
    _mk_blockdev(tmp_path, "md0")
    _mk_blockdev(tmp_path, "dm-0")
    _mk_blockdev(tmp_path, "sda")
    out = mod.walk_block_devs(str(tmp_path / "block"))
    assert [d["name"] for d in out] == ["sda"]


def test_walk_skips_devs_without_queue(tmp_path):
    d = tmp_path / "block" / "weird"
    d.mkdir(parents=True)
    out = mod.walk_block_devs(str(tmp_path / "block"))
    assert out == []


# --- _find_provisioning_mode -----------------------------------

def test_provisioning_mode_present(tmp_path):
    _mk_blockdev(tmp_path, "sda", provisioning_mode="unmap")
    dev = str(tmp_path / "block" / "sda")
    assert mod._find_provisioning_mode(dev) == "unmap"


def test_provisioning_mode_absent(tmp_path):
    _mk_blockdev(tmp_path, "sda")  # no SCSI dir
    dev = str(tmp_path / "block" / "sda")
    assert mod._find_provisioning_mode(dev) is None


# --- classify --------------------------------------------------

def _dev(*, name="sda", discard_max=1024**3,
         discard_gran=4096, rotational=0,
         provisioning_mode=""):
    return {"name": name, "discard_max": discard_max,
            "discard_gran": discard_gran,
            "rotational": rotational,
            "provisioning_mode": provisioning_mode}


def test_classify_unknown_empty():
    v = mod.classify([])
    assert v["verdict"] == "unknown"


def test_classify_sane():
    v = mod.classify([_dev()])
    assert v["verdict"] == "discard_sane"


def test_classify_ssd_with_discard_disabled_err():
    v = mod.classify(
        [_dev(rotational=0, discard_max=0)])
    assert v["verdict"] == "discard_disabled_on_ssd"


def test_classify_rotational_with_discard_disabled_ok():
    # CDROM / spinning disk with no TRIM is normal
    v = mod.classify(
        [_dev(rotational=1, discard_max=0)])
    assert v["verdict"] == "discard_sane"


def test_classify_provisioning_full_warn():
    v = mod.classify([_dev(provisioning_mode="full")])
    assert v["verdict"] == "provisioning_mode_full"


def test_classify_huge_granularity_accent():
    v = mod.classify(
        [_dev(discard_gran=2 * 1024 * 1024)])
    assert v["verdict"] == "discard_granularity_huge"


# Priority : disabled_on_ssd > provisioning_full > huge_gran
def test_priority_ssd_over_provisioning():
    v = mod.classify([
        _dev(rotational=0, discard_max=0,
             provisioning_mode="full"),
    ])
    assert v["verdict"] == "discard_disabled_on_ssd"


def test_priority_provisioning_over_gran():
    v = mod.classify([
        _dev(provisioning_mode="full",
             discard_gran=2 * 1024 * 1024),
    ])
    assert v["verdict"] == "provisioning_mode_full"


# --- status integration ----------------------------------------

def test_status_unknown(tmp_path):
    out = mod.status(None, str(tmp_path / "nope"))
    assert out["verdict"]["verdict"] == "unknown"


def test_status_sane_synthetic(tmp_path):
    _mk_blockdev(tmp_path, "sda")
    out = mod.status(None, str(tmp_path / "block"))
    assert out["verdict"]["verdict"] == "discard_sane"
    assert out["device_count"] == 1


def test_status_ssd_disabled_synthetic(tmp_path):
    _mk_blockdev(tmp_path, "sda",
                       discard_max="0", rotational="0")
    out = mod.status(None, str(tmp_path / "block"))
    assert (out["verdict"]["verdict"]
            == "discard_disabled_on_ssd")
    assert out["ok"] is False
