"""Tests for modules/v4l2_media_audit.py — R&D #74.4."""
from __future__ import annotations

import os

import pytest

from gpu_dashboard.modules import v4l2_media_audit as mod


def _mk_v4l(root, name, *, driver="uvcvideo"):
    d = root / name
    d.mkdir(parents=True, exist_ok=True)
    if driver:
        dev = d / "device"; dev.mkdir(exist_ok=True)
        # Use a relative path that's valid as a symlink target
        target = "/sys/bus/usb/drivers/" + driver
        try:
            os.symlink(target, str(dev / "driver"))
        except FileExistsError:
            pass


# --- list_class ------------------------------------------------

def test_list_missing(tmp_path):
    assert mod.list_class(str(tmp_path / "nope")) == []


def test_list_v4l_with_driver(tmp_path):
    _mk_v4l(tmp_path, "video0", driver="uvcvideo")
    _mk_v4l(tmp_path, "video1", driver="bttv")
    out = mod.list_class(str(tmp_path))
    by_name = {v["name"]: v for v in out}
    assert by_name["video0"]["driver"] == "uvcvideo"


def test_list_v4l_orphan(tmp_path):
    # Build entry without device/driver symlink
    (tmp_path / "video0").mkdir()
    out = mod.list_class(str(tmp_path))
    assert out[0]["driver"] is None


# --- classify ---------------------------------------------------

def test_classify_unknown():
    v = mod.classify([], [], [], False, False, False, [])
    assert v["verdict"] == "unknown"


def test_classify_ok():
    v = mod.classify(
        [{"name": "video0", "driver": "uvcvideo"}],
        [{"name": "media0", "driver": "uvcvideo"}],
        [], True, True, False, [])
    assert v["verdict"] == "ok"


def test_classify_device_root_only():
    v = mod.classify(
        [{"name": "video0", "driver": "uvcvideo"}],
        [{"name": "media0", "driver": "uvcvideo"}],
        [], True, True, False, ["video0"])
    assert v["verdict"] == "device_root_only_blocks_users"


def test_classify_driver_missing():
    v = mod.classify(
        [{"name": "video0", "driver": None}],
        [{"name": "media0", "driver": "uvcvideo"}],
        [], True, True, False, [])
    assert v["verdict"] == "driver_missing_kernel_module"


def test_classify_capture_no_media_controller():
    v = mod.classify(
        [{"name": "video0", "driver": "uvcvideo"}],
        [],
        [], True, True, False, [])
    assert v["verdict"] == "capture_node_present_no_media_controller"


def test_classify_stale_v4l_empty_dir():
    v = mod.classify([], [], [], True, False, False, [])
    assert v["verdict"] == "stale_v4l_no_driver"


# Priority : perms > driver_missing > no_media > stale
def test_priority_perms_over_driver_missing():
    v = mod.classify(
        [{"name": "video0", "driver": None}],
        [{"name": "media0", "driver": "uvcvideo"}],
        [], True, True, False, ["video0"])
    assert v["verdict"] == "device_root_only_blocks_users"


def test_priority_driver_missing_over_no_media():
    v = mod.classify(
        [{"name": "video0", "driver": None}],
        [],
        [], True, True, False, [])
    assert v["verdict"] == "driver_missing_kernel_module"


# --- status integration -----------------------------------------

def test_status_unknown(tmp_path):
    out = mod.status(None,
                          str(tmp_path / "no_v4l"),
                          str(tmp_path / "no_media"),
                          str(tmp_path / "no_cec"),
                          str(tmp_path / "no_dev"))
    assert out["ok"] is False
    assert out["verdict"]["verdict"] == "unknown"


def test_status_stale_synthetic(tmp_path):
    v4l = tmp_path / "v4l"; v4l.mkdir()  # empty
    out = mod.status(None, str(v4l),
                          str(tmp_path / "no_media"),
                          str(tmp_path / "no_cec"),
                          str(tmp_path / "no_dev"))
    assert out["ok"] is True
    assert out["verdict"]["verdict"] == "stale_v4l_no_driver"


def test_status_ok_synthetic(tmp_path):
    v4l = tmp_path / "v4l"; v4l.mkdir()
    _mk_v4l(v4l, "video0", driver="uvcvideo")
    media = tmp_path / "media"; media.mkdir()
    _mk_v4l(media, "media0", driver="uvcvideo")
    # /dev nodes with 0660 → not flagged
    dev_root = tmp_path / "dev"; dev_root.mkdir()
    (dev_root / "video0").write_text("")
    os.chmod(str(dev_root / "video0"), 0o660)
    (dev_root / "media0").write_text("")
    os.chmod(str(dev_root / "media0"), 0o660)
    out = mod.status(None, str(v4l), str(media),
                          str(tmp_path / "no_cec"),
                          str(dev_root))
    assert out["verdict"]["verdict"] == "ok"


def test_status_perms_synthetic(tmp_path):
    v4l = tmp_path / "v4l"; v4l.mkdir()
    _mk_v4l(v4l, "video0", driver="uvcvideo")
    media = tmp_path / "media"; media.mkdir()
    _mk_v4l(media, "media0", driver="uvcvideo")
    dev_root = tmp_path / "dev"; dev_root.mkdir()
    (dev_root / "video0").write_text("")
    os.chmod(str(dev_root / "video0"), 0o600)
    (dev_root / "media0").write_text("")
    os.chmod(str(dev_root / "media0"), 0o660)
    out = mod.status(None, str(v4l), str(media),
                          str(tmp_path / "no_cec"),
                          str(dev_root))
    # When running as root, chmod 0o600 may still be readable;
    # but the audit only checks mode bits not actual access.
    assert out["verdict"]["verdict"] == \
        "device_root_only_blocks_users"
