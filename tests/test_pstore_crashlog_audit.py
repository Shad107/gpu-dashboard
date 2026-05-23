"""Tests for modules/pstore_crashlog_audit.py — R&D #68.1."""
from __future__ import annotations

import os

import pytest

from gpu_dashboard.modules import pstore_crashlog_audit as mod


# --- is_pstore_mounted ------------------------------------------

def test_is_pstore_mounted_missing(tmp_path):
    assert mod.is_pstore_mounted(str(tmp_path / "nope")) is False


def test_is_pstore_mounted_no(tmp_path):
    p = tmp_path / "mounts"
    p.write_text("proc /proc proc rw 0 0\n"
                    "sysfs /sys sysfs rw 0 0\n")
    assert mod.is_pstore_mounted(str(p)) is False


def test_is_pstore_mounted_yes(tmp_path):
    p = tmp_path / "mounts"
    p.write_text("proc /proc proc rw 0 0\n"
                    "none /sys/fs/pstore pstore "
                    "rw,nosuid,nodev,noexec,relatime 0 0\n")
    assert mod.is_pstore_mounted(str(p)) is True


# --- read_backend -----------------------------------------------

def test_read_backend_missing(tmp_path):
    assert mod.read_backend(str(tmp_path / "nope")) is None


def test_read_backend_null(tmp_path):
    p = tmp_path / "backend"
    p.write_text("(null)\n")
    assert mod.read_backend(str(p)) is None


def test_read_backend_efi(tmp_path):
    p = tmp_path / "backend"
    p.write_text("efi_pstore\n")
    assert mod.read_backend(str(p)) == "efi_pstore"


def test_read_backend_empty(tmp_path):
    p = tmp_path / "backend"
    p.write_text("\n")
    assert mod.read_backend(str(p)) is None


# --- list_pstore_entries ----------------------------------------

def test_list_pstore_entries_missing(tmp_path):
    out = mod.list_pstore_entries(str(tmp_path / "nope"))
    assert out == {"present": False, "eacces": False, "files": []}


def test_list_pstore_entries_empty(tmp_path):
    out = mod.list_pstore_entries(str(tmp_path))
    assert out["present"] is True
    assert out["eacces"] is False
    assert out["files"] == []


def test_list_pstore_entries_files(tmp_path):
    (tmp_path / "dmesg-efi_pstore-12345").write_text("x" * 200)
    (tmp_path / "panic-efi_pstore-67890").write_text("y" * 100)
    # Files that don't match the prefix list are ignored.
    (tmp_path / "README").write_text("ignore me")
    out = mod.list_pstore_entries(str(tmp_path))
    names = sorted(e["name"] for e in out["files"])
    assert names == ["dmesg-efi_pstore-12345",
                       "panic-efi_pstore-67890"]
    sizes = {e["name"]: e["size"] for e in out["files"]}
    assert sizes["dmesg-efi_pstore-12345"] == 200


# --- classify ---------------------------------------------------

def test_classify_unknown_not_mounted():
    v = mod.classify(False, None, {"present": False,
                                              "eacces": False,
                                              "files": []})
    assert v["verdict"] == "unknown"


def test_classify_stale_panic_logs_present():
    v = mod.classify(True, "efi_pstore",
                          {"present": True, "eacces": False,
                            "files": [{"name": "dmesg-x",
                                          "size": 100}]})
    assert v["verdict"] == "stale_panic_logs_present"
    assert "dmesg-x" in v["reason"]


def test_classify_backend_absent():
    v = mod.classify(True, None,
                          {"present": True, "eacces": False,
                            "files": []})
    assert v["verdict"] == "pstore_backend_absent"


def test_classify_requires_root():
    v = mod.classify(True, "efi_pstore",
                          {"present": True, "eacces": True,
                            "files": []})
    assert v["verdict"] == "requires_root"


def test_classify_ok():
    v = mod.classify(True, "efi_pstore",
                          {"present": True, "eacces": False,
                            "files": []})
    assert v["verdict"] == "ok"


# Priority : stale_panic_logs > backend_absent > requires_root
def test_priority_stale_over_backend_absent():
    v = mod.classify(True, None,
                          {"present": True, "eacces": False,
                            "files": [{"name": "dmesg-x",
                                          "size": 100}]})
    assert v["verdict"] == "stale_panic_logs_present"


def test_priority_backend_absent_over_requires_root():
    v = mod.classify(True, None,
                          {"present": True, "eacces": True,
                            "files": []})
    assert v["verdict"] == "pstore_backend_absent"


# --- status integration -----------------------------------------

def test_status_unknown(tmp_path):
    mounts = tmp_path / "mounts"
    mounts.write_text("proc /proc proc rw 0 0\n")
    out = mod.status(None, str(mounts),
                          str(tmp_path / "nope"),
                          str(tmp_path / "no-backend"))
    assert out["ok"] is False
    assert out["verdict"]["verdict"] == "unknown"


def test_status_ok_synthetic(tmp_path):
    mounts = tmp_path / "mounts"
    mounts.write_text("none /sys/fs/pstore pstore rw 0 0\n")
    backend = tmp_path / "backend"
    backend.write_text("efi_pstore\n")
    pstore_dir = tmp_path / "pstore"; pstore_dir.mkdir()
    out = mod.status(None, str(mounts), str(pstore_dir),
                          str(backend))
    assert out["ok"] is True
    assert out["backend"] == "efi_pstore"
    assert out["verdict"]["verdict"] == "ok"


def test_status_stale_synthetic(tmp_path):
    mounts = tmp_path / "mounts"
    mounts.write_text("none /sys/fs/pstore pstore rw 0 0\n")
    backend = tmp_path / "backend"
    backend.write_text("efi_pstore\n")
    pstore_dir = tmp_path / "pstore"; pstore_dir.mkdir()
    (pstore_dir / "dmesg-efi_pstore-1").write_text("panic")
    out = mod.status(None, str(mounts), str(pstore_dir),
                          str(backend))
    assert out["verdict"]["verdict"] == "stale_panic_logs_present"
    assert out["entry_count"] == 1
