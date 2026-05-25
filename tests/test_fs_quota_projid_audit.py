"""Tests for modules/fs_quota_projid_audit.py R&D #98.2."""
from __future__ import annotations

import pytest

from gpu_dashboard.modules import fs_quota_projid_audit as mod


# --- parse_proc_mounts -----------------------------------------

def test_parse_mounts_empty():
    assert mod.parse_proc_mounts("") == []


def test_parse_mounts_skips_no_quota():
    text = (
        "/dev/sda1 / ext4 rw,relatime 0 0\n"
        "proc /proc proc rw 0 0\n")
    assert mod.parse_proc_mounts(text) == []


def test_parse_mounts_prjquota():
    text = (
        "/dev/sda1 / xfs rw,prjquota,relatime 0 0\n"
        "/dev/sdb1 /data xfs rw,usrquota,grpquota 0 0\n")
    out = mod.parse_proc_mounts(text)
    assert len(out) == 2
    assert "prjquota" in out[0]["quota_opts"]
    assert "usrquota" in out[1]["quota_opts"]
    assert "grpquota" in out[1]["quota_opts"]


# --- parse_mountinfo_overlays ----------------------------------

def test_parse_overlays_empty():
    assert mod.parse_mountinfo_overlays("") == []


def test_parse_overlays_basic():
    # Standard mountinfo line for overlayfs
    text = (
        "100 99 0:50 / /var/lib/docker/overlay2/abc/merged "
        "rw,relatime shared:1 - overlay overlay "
        "rw,lowerdir=/L,upperdir=/data/upper,workdir=/W\n")
    out = mod.parse_mountinfo_overlays(text)
    assert len(out) == 1
    assert out[0]["upperdir"] == "/data/upper"


def test_parse_overlays_skips_non_overlay():
    text = (
        "36 35 0:1 / /proc rw - proc proc rw\n"
        "37 36 0:2 / /sys rw - sysfs sysfs rw\n")
    assert mod.parse_mountinfo_overlays(text) == []


# --- find_quota_for_path ---------------------------------------

def test_find_quota_for_path_match():
    quotas = [
        {"mountpoint": "/data", "quota_opts": ["prjquota"]},
        {"mountpoint": "/var",  "quota_opts": ["usrquota"]},
    ]
    m = mod.find_quota_for_path(quotas, "/data/upper/x")
    assert m is not None
    assert m["mountpoint"] == "/data"


def test_find_quota_for_path_most_specific():
    quotas = [
        {"mountpoint": "/",     "quota_opts": ["usrquota"]},
        {"mountpoint": "/data", "quota_opts": ["prjquota"]},
    ]
    m = mod.find_quota_for_path(quotas, "/data/upper")
    assert m["mountpoint"] == "/data"


def test_find_quota_for_path_no_match():
    quotas = [{"mountpoint": "/data",
               "quota_opts": ["prjquota"]}]
    assert mod.find_quota_for_path(
        quotas, "/var/upper") is None


# --- classify --------------------------------------------------

def _q(*, mp="/data", opts=None):
    return {"device": "/dev/sda1", "mountpoint": mp,
            "fstype": "xfs",
            "quota_opts": opts or ["prjquota"]}


def test_classify_unknown_no_mounts():
    v = mod.classify(False, False, [], [], False, False)
    assert v["verdict"] == "unknown"


def test_classify_requires_root():
    v = mod.classify(True, False, [], [], False, False)
    assert v["verdict"] == "requires_root"


def test_classify_ok_no_quotas():
    v = mod.classify(True, True, [], [], False, False)
    assert v["verdict"] == "ok"


def test_classify_overlay_warn():
    v = mod.classify(
        True, True, [_q(mp="/data")],
        [{"mountpoint": "/var/lib/docker/overlay/x",
          "upperdir": "/data/upper"}],
        True, True)
    assert v["verdict"] == "overlay_upper_on_quota_fs"


def test_classify_orphan_quota_accent():
    v = mod.classify(
        True, True, [_q(opts=["prjquota"])],
        [], False, True)
    assert v["verdict"] == "orphan_quota_config"


def test_classify_no_tools_accent():
    v = mod.classify(
        True, True, [_q(opts=["usrquota"])],
        [], False, False)
    assert v["verdict"] == "quota_enabled_no_tools"


def test_classify_ok_with_quotas():
    v = mod.classify(
        True, True, [_q(opts=["prjquota"])],
        [], True, True)
    assert v["verdict"] == "ok"


# Priority : overlay > orphan_prj > no_tools
def test_priority_overlay_over_orphan():
    v = mod.classify(
        True, True, [_q(opts=["prjquota"])],
        [{"mountpoint": "/var/lib/docker/overlay/x",
          "upperdir": "/data/upper"}],
        False, True)
    assert v["verdict"] == "overlay_upper_on_quota_fs"


def test_priority_orphan_over_no_tools():
    v = mod.classify(
        True, True, [_q(opts=["prjquota"])],
        [], False, False)
    assert v["verdict"] == "orphan_quota_config"


# --- status integration ----------------------------------------

def test_status_unknown(tmp_path):
    out = mod.status(None,
                       str(tmp_path / "no_mounts"),
                       str(tmp_path / "no_mountinfo"),
                       str(tmp_path / "no_projects"))
    assert out["verdict"]["verdict"] == "unknown"


def test_status_ok_no_quotas(tmp_path):
    m = tmp_path / "mounts"
    m.write_text("/dev/sda1 / ext4 rw,relatime 0 0\n")
    mi = tmp_path / "mountinfo"
    mi.write_text("")
    out = mod.status(None, str(m), str(mi),
                       str(tmp_path / "no_proj"))
    assert out["verdict"]["verdict"] == "ok"
    assert out["quota_mount_count"] == 0
