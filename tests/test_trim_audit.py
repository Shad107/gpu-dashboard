"""R&D #25.2 — TRIM / discard auditor tests."""
import os
import pytest
from unittest.mock import patch
from gpu_dashboard.modules import trim_audit as ta


# ── device_basename ────────────────────────────────────────────────────


def test_basename_nvme():
    assert ta.device_basename("/dev/nvme0n1p2") == "nvme0n1"


def test_basename_nvme_no_partition():
    assert ta.device_basename("/dev/nvme1n1") == "nvme1n1"


def test_basename_sata():
    assert ta.device_basename("/dev/sda1") == "sda"
    assert ta.device_basename("/dev/sdb") == "sdb"


def test_basename_not_dev():
    assert ta.device_basename("tmpfs") is None
    assert ta.device_basename("/foo/bar") is None


# ── is_rotational ──────────────────────────────────────────────────────


def test_rotational_hdd(tmp_path):
    qd = tmp_path / "sda" / "queue"
    qd.mkdir(parents=True)
    (qd / "rotational").write_text("1\n")
    assert ta.is_rotational("sda", sys_root=str(tmp_path)) is True


def test_rotational_ssd(tmp_path):
    qd = tmp_path / "nvme0n1" / "queue"
    qd.mkdir(parents=True)
    (qd / "rotational").write_text("0\n")
    assert ta.is_rotational("nvme0n1", sys_root=str(tmp_path)) is False


def test_rotational_missing(tmp_path):
    assert ta.is_rotational("nvme99", sys_root=str(tmp_path)) is None


# ── parse_proc_mounts / find_mount_for ─────────────────────────────────


def test_parse_mounts(tmp_path):
    p = tmp_path / "mounts"
    p.write_text("/dev/nvme0n1p2 / ext4 rw,relatime,discard 0 0\n")
    out = ta.parse_proc_mounts(str(p))
    assert len(out) == 1
    assert "discard" in out[0]["options"]


def test_find_mount_longest_prefix():
    mounts = [
        {"mountpoint": "/", "options": [], "fstype": "ext4", "device": "/dev/sda"},
        {"mountpoint": "/home", "options": ["discard"], "fstype": "ext4",
         "device": "/dev/sdb"},
    ]
    m = ta.find_mount_for("/home/u/x", mounts)
    assert m["mountpoint"] == "/home"


# ── audit_one_dir ──────────────────────────────────────────────────────


def test_audit_missing_dir(tmp_path):
    mounts = [{"mountpoint": "/", "options": [], "fstype": "ext4",
                "device": "/dev/sda1"}]
    assert ta.audit_one_dir(str(tmp_path / "nonexist"), mounts) is None


def test_audit_existing_dir_discard(tmp_path, monkeypatch):
    mounts = [{"mountpoint": str(tmp_path), "options": ["rw", "discard"],
                "fstype": "ext4", "device": "/dev/nvme0n1p1"}]
    with patch.object(ta, "is_rotational", return_value=False):
        out = ta.audit_one_dir(str(tmp_path), mounts)
    assert out is not None
    assert out["has_discard_mount"] is True
    assert out["on_ssd"] is True


def test_audit_network_fs():
    """NFS mount → on_ssd is False because TRIM doesn't apply."""
    mounts = [{"mountpoint": "/nfs", "options": ["rw"], "fstype": "nfs4",
                "device": "host:/share"}]
    import os.path
    with patch("os.path.isdir", return_value=True):
        out = ta.audit_one_dir("/nfs", mounts)
    assert out["on_ssd"] is False
    assert out["fstype"] == "nfs4"


def test_audit_tmpfs():
    mounts = [{"mountpoint": "/tmp", "options": ["rw"], "fstype": "tmpfs",
                "device": "tmpfs"}]
    with patch("os.path.isdir", return_value=True):
        out = ta.audit_one_dir("/tmp", mounts)
    assert out["on_ssd"] is False


# ── classify ───────────────────────────────────────────────────────────


def test_classify_no_dirs():
    v = ta.classify([], {"enabled": "enabled", "active": "active"})
    assert v["verdict"] == "no_dirs"


def test_classify_no_ssd():
    audits = [{"directory": "/x", "has_discard_mount": False,
                "on_ssd": False, "fstype": "ext4"}]
    v = ta.classify(audits, {"enabled": "enabled", "active": "active"})
    assert v["verdict"] == "no_ssd"


def test_classify_ok_with_timer():
    audits = [{"directory": "/x", "has_discard_mount": False,
                "on_ssd": True, "fstype": "ext4"}]
    v = ta.classify(audits, {"enabled": "enabled", "active": "active"})
    assert v["verdict"] == "ok"


def test_classify_ok_with_inline_discard():
    audits = [{"directory": "/x", "has_discard_mount": True,
                "on_ssd": True, "fstype": "ext4"}]
    v = ta.classify(audits, {"enabled": "disabled", "active": "inactive"})
    assert v["verdict"] == "ok"


def test_classify_no_trim_warns():
    audits = [{"directory": "/x", "has_discard_mount": False,
                "on_ssd": True, "fstype": "ext4"}]
    v = ta.classify(audits, {"enabled": "disabled", "active": "inactive"})
    assert v["verdict"] == "no_trim"
    assert "fstrim.timer" in v["recommendation"]


# ── status ─────────────────────────────────────────────────────────────


def test_status_aggregates(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    (tmp_path / "ComfyUI" / "models").mkdir(parents=True)
    with patch.object(ta, "parse_proc_mounts",
                      return_value=[{"mountpoint": str(tmp_path),
                                      "options": ["rw"],
                                      "fstype": "ext4",
                                      "device": "/dev/nvme0n1p1"}]):
        with patch.object(ta, "is_rotational", return_value=False):
            with patch.object(ta, "fstrim_timer_state",
                              return_value={"enabled": "enabled",
                                             "active": "active"}):
                s = ta.status()
    assert s["ok"] is True
    assert s["audit_count"] >= 1
    assert s["verdict"]["verdict"] == "ok"
