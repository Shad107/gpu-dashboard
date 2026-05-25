"""Tests for modules/nfs_mountstats_audit.py — R&D #93.3."""
from __future__ import annotations

import pytest

from gpu_dashboard.modules import nfs_mountstats_audit as mod


_NFS_SECTION = (
    "device 192.168.1.10:/export mounted on /mnt/nfs "
    "with fstype nfs4 statvers=1.1\n"
    "        opts: rw,vers=4.2\n"
    "        age: 1234\n"
    "        events: 12 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0\n"
    "        bytes: 0 0 0 0 0 0 0 0\n"
    "        RPC iostats version: 1.1\n"
    "        xprt: tcp 12345 1 1 0 100 5000 4998 0 0 0\n")


def _mk_mountstats(tmp_path, text=_NFS_SECTION,
                    name="mountstats"):
    p = tmp_path / name
    p.write_text(text)
    return str(p)


# --- parse_mountstats ------------------------------------------

def test_parse_mountstats_empty():
    assert mod.parse_mountstats("") == []


def test_parse_mountstats_no_nfs():
    text = (
        "device /dev/sda1 mounted on / with fstype ext4\n"
        "        opts: rw\n")
    assert mod.parse_mountstats(text) == []


def test_parse_mountstats_nfs():
    out = mod.parse_mountstats(_NFS_SECTION)
    assert len(out) == 1
    assert out[0]["device"] == "192.168.1.10:/export"
    assert out[0]["mountpoint"] == "/mnt/nfs"
    assert out[0]["connect_count"] == 1
    assert out[0]["sends"] == 5000
    assert out[0]["bad_xids"] == 0


def test_parse_mountstats_multiple_nfs():
    text = _NFS_SECTION + (
        "device 192.168.1.11:/srv mounted on /mnt/other "
        "with fstype nfs4\n"
        "        xprt: tcp 12346 1 7 0 0 1000 1000 3 0 0\n")
    out = mod.parse_mountstats(text)
    assert len(out) == 2
    assert out[1]["connect_count"] == 7
    assert out[1]["bad_xids"] == 3


def test_parse_mountstats_skips_non_tcp():
    text = (
        "device 192.168.1.10:/x mounted on /y with fstype nfs\n"
        "        xprt: udp 12345 1 1 100 1000 1000\n")
    out = mod.parse_mountstats(text)
    assert len(out) == 1
    assert out[0]["sends"] is None  # not parsed (udp)


# --- classify --------------------------------------------------

def _mount(*, device="d", mountpoint="/m", fstype="nfs4",
            connect_count=1, sends=1000, recvs=999,
            bad_xids=0):
    return {"device": device, "mountpoint": mountpoint,
            "fstype": fstype, "connect_count": connect_count,
            "sends": sends, "recvs": recvs,
            "bad_xids": bad_xids}


def test_classify_unknown_no_file():
    v = mod.classify([], False, False)
    assert v["verdict"] == "unknown"


def test_classify_requires_root():
    v = mod.classify([], True, False)
    assert v["verdict"] == "requires_root"


def test_classify_no_nfs_mounts():
    v = mod.classify([], True, True)
    assert v["verdict"] == "no_nfs_mounts"


def test_classify_ok():
    v = mod.classify([_mount()], True, True)
    assert v["verdict"] == "ok"


def test_classify_reconnect_storm():
    v = mod.classify([_mount(connect_count=10)],
                          True, True)
    assert v["verdict"] == "xprt_reconnect_storm"


def test_classify_storm_at_threshold_is_ok():
    # exactly 5 is at threshold, NOT > 5
    v = mod.classify([_mount(connect_count=5)],
                          True, True)
    assert v["verdict"] == "ok"


def test_classify_bad_xids():
    v = mod.classify([_mount(bad_xids=3)],
                          True, True)
    assert v["verdict"] == "bad_xids_present"


def test_classify_many_mounts():
    mounts = [_mount(device=f"d{i}") for i in range(15)]
    v = mod.classify(mounts, True, True)
    assert v["verdict"] == "many_nfs_mounts"


# Priority : reconnect > bad_xids > many > ok
def test_priority_reconnect_over_bad_xids():
    v = mod.classify([_mount(connect_count=10, bad_xids=5)],
                          True, True)
    assert v["verdict"] == "xprt_reconnect_storm"


def test_priority_bad_xids_over_many():
    mounts = [_mount(device=f"d{i}") for i in range(15)]
    mounts[0]["bad_xids"] = 3
    v = mod.classify(mounts, True, True)
    assert v["verdict"] == "bad_xids_present"


# --- status integration ----------------------------------------

def test_status_unknown_no_file(tmp_path):
    out = mod.status(None, str(tmp_path / "nope"))
    assert out["verdict"]["verdict"] == "unknown"


def test_status_no_nfs_synthetic(tmp_path):
    p = _mk_mountstats(tmp_path,
                              "device /dev/sda1 mounted on / "
                              "with fstype ext4\n")
    out = mod.status(None, p)
    assert out["verdict"]["verdict"] == "no_nfs_mounts"
    assert out["ok"] is True


def test_status_ok_nfs_synthetic(tmp_path):
    p = _mk_mountstats(tmp_path)
    out = mod.status(None, p)
    assert out["verdict"]["verdict"] == "ok"
    assert out["nfs_mount_count"] == 1


def test_status_reconnect_storm_synthetic(tmp_path):
    text = (
        "device 192.168.1.10:/x mounted on /m "
        "with fstype nfs4 statvers=1.1\n"
        "        xprt: tcp 12345 1 25 0 0 100 100 0 0 0\n")
    p = _mk_mountstats(tmp_path, text)
    out = mod.status(None, p)
    assert (out["verdict"]["verdict"]
            == "xprt_reconnect_storm")
    assert out["ok"] is False
