"""R&D #23.2 — FS mount-option auditor tests."""
import os
import pytest
from gpu_dashboard.modules import fs_mount_audit as fa


# ── parse_proc_mounts ──────────────────────────────────────────────────


def test_parse_simple(tmp_path):
    p = tmp_path / "mounts"
    p.write_text("/dev/sda1 / ext4 rw,relatime 0 0\n"
                  "tmpfs /tmp tmpfs rw,nosuid 0 0\n")
    out = fa.parse_proc_mounts(str(p))
    assert len(out) == 2
    assert out[0]["device"] == "/dev/sda1"
    assert out[0]["fstype"] == "ext4"
    assert "rw" in out[0]["options"]


def test_parse_missing():
    assert fa.parse_proc_mounts("/nonexistent") == []


def test_parse_handles_octal_escapes(tmp_path):
    p = tmp_path / "mounts"
    p.write_text("/dev/sda1 /path\\040with\\040spaces ext4 rw 0 0\n")
    out = fa.parse_proc_mounts(str(p))
    assert out[0]["mountpoint"] == "/path with spaces"


# ── find_mount_for ─────────────────────────────────────────────────────


def test_find_mount_root_match():
    mounts = [{"mountpoint": "/", "fstype": "ext4", "options": []}]
    assert fa.find_mount_for("/home/u/file", mounts)["mountpoint"] == "/"


def test_find_mount_longest_prefix():
    mounts = [
        {"mountpoint": "/", "fstype": "ext4", "options": []},
        {"mountpoint": "/home", "fstype": "btrfs", "options": []},
        {"mountpoint": "/home/u/models", "fstype": "xfs", "options": []},
    ]
    m = fa.find_mount_for("/home/u/models/x", mounts)
    assert m["fstype"] == "xfs"


def test_find_mount_no_match():
    mounts = [{"mountpoint": "/foo", "fstype": "ext4", "options": []}]
    assert fa.find_mount_for("/bar/baz", mounts) is None


def test_find_mount_exact_path_match():
    mounts = [{"mountpoint": "/home/u/models", "fstype": "xfs", "options": []}]
    m = fa.find_mount_for("/home/u/models", mounts)
    assert m is not None


# ── classify_mount ─────────────────────────────────────────────────────


def test_classify_ok_ext4():
    m = {"fstype": "ext4", "options": ["rw", "relatime"]}
    out = fa.classify_mount(m)
    assert out["severity"] == "ok"
    assert out["issues"] == []


def test_classify_warn_btrfs_compress():
    m = {"fstype": "btrfs", "options": ["rw", "compress=zstd:3"]}
    out = fa.classify_mount(m)
    assert out["severity"] == "warn"
    assert any("btrfs compression" in i["label"] for i in out["issues"])


def test_classify_warn_nfs():
    m = {"fstype": "nfs4", "options": ["rw"]}
    out = fa.classify_mount(m)
    assert out["severity"] == "warn"
    # Both 'network filesystem' AND 'atime' issues should appear
    labels = [i["label"] for i in out["issues"]]
    assert any("network filesystem" in l for l in labels)
    assert any("atime updates" in l for l in labels)


def test_classify_nfs_with_noatime_only_one_issue():
    m = {"fstype": "nfs4", "options": ["rw", "noatime"]}
    out = fa.classify_mount(m)
    labels = [i["label"] for i in out["issues"]]
    assert any("network filesystem" in l for l in labels)
    assert not any("atime updates" in l for l in labels)


def test_classify_fail_ecryptfs():
    m = {"fstype": "ecryptfs", "options": ["rw"]}
    out = fa.classify_mount(m)
    assert out["severity"] == "fail"


def test_classify_warn_data_journal():
    m = {"fstype": "ext4", "options": ["rw", "data=journal"]}
    out = fa.classify_mount(m)
    assert out["severity"] == "warn"
    assert any("data=journal" in i["label"] for i in out["issues"])


def test_classify_warn_tmpfs():
    m = {"fstype": "tmpfs", "options": ["rw"]}
    out = fa.classify_mount(m)
    assert out["severity"] == "warn"


# ── audit_known_dirs ───────────────────────────────────────────────────


def test_audit_skips_missing_dirs(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    # None of the known_dirs exist on this synthetic HOME
    out = fa.audit_known_dirs(mounts=[
        {"mountpoint": "/", "fstype": "ext4", "options": []}])
    assert out == []


def test_audit_reports_known_dir(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    target = tmp_path / "ComfyUI" / "models"
    target.mkdir(parents=True)
    mounts = [{"mountpoint": str(tmp_path), "fstype": "btrfs",
                "options": ["rw", "compress=zstd:3"]}]
    out = fa.audit_known_dirs(mounts=mounts)
    assert len(out) == 1
    assert out[0]["fstype"] == "btrfs"
    assert out[0]["severity"] == "warn"


# ── status ─────────────────────────────────────────────────────────────


def test_status_no_dirs(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    # parse real /proc/mounts but the synthetic home has no model dirs
    s = fa.status()
    assert s["audit_count"] == 0
    assert s["verdict"]["verdict"] == "no_dirs"


def test_status_warn_count(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    (tmp_path / "ComfyUI" / "models").mkdir(parents=True)
    proc_mounts = tmp_path / "mounts"
    proc_mounts.write_text(
        f"/dev/sda1 {tmp_path} btrfs rw,compress=zstd:3 0 0\n"
    )
    # We can't easily monkey patch parse_proc_mounts globally,
    # but we test via audit_known_dirs which accepts mounts arg.
    mounts = fa.parse_proc_mounts(str(proc_mounts))
    audits = fa.audit_known_dirs(mounts=mounts)
    assert len(audits) >= 1
    assert audits[0]["severity"] == "warn"
