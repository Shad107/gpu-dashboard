"""Tests for modules/io_uring_runtime_audit.py — R&D #54.4."""
from __future__ import annotations

import pytest

from gpu_dashboard.modules import io_uring_runtime_audit as mod


def _mk_sysctl(root, *, disabled=None, group=None):
    root.mkdir(parents=True, exist_ok=True)
    if disabled is not None:
        (root / "io_uring_disabled").write_text(f"{disabled}\n")
    if group is not None:
        (root / "io_uring_group").write_text(f"{group}\n")


# --- parse_kernel_release ---------------------------------------

def test_parse_kernel_release():
    assert mod.parse_kernel_release("6.17.0-29-generic") == (6, 17)
    assert mod.parse_kernel_release("5.4.0-200-generic") == (5, 4)
    assert mod.parse_kernel_release("6.1") == (6, 1)


def test_parse_kernel_release_garbage():
    assert mod.parse_kernel_release("") is None
    assert mod.parse_kernel_release("foo") is None


# --- read_state -------------------------------------------------

def test_read_state_missing(tmp_path):
    out = mod.read_state(str(tmp_path / "nodisabled"),
                            str(tmp_path / "nogroup"),
                            str(tmp_path / "nodebugfs"))
    assert out["sysctl_present"] is False
    assert out["debugfs_present"] is False
    assert out["io_uring_disabled"] is None


def test_read_state_full(tmp_path):
    _mk_sysctl(tmp_path, disabled=0, group=-1)
    debugfs = tmp_path / "debugfs"
    debugfs.mkdir()
    out = mod.read_state(
        str(tmp_path / "io_uring_disabled"),
        str(tmp_path / "io_uring_group"),
        str(debugfs))
    assert out["io_uring_disabled"] == 0
    assert out["io_uring_group"] == -1
    assert out["sysctl_present"] is True
    assert out["debugfs_present"] is True
    assert out["debugfs_readable"] is True


# --- classify ---------------------------------------------------

def _state(disabled=0, group=-1, sysctl_present=True,
            debugfs_present=False, debugfs_readable=True):
    return {"io_uring_disabled": disabled,
              "io_uring_group": group,
              "sysctl_present": sysctl_present,
              "debugfs_present": debugfs_present,
              "debugfs_readable": debugfs_readable}


def test_classify_unknown():
    v = mod.classify(_state(disabled=None, sysctl_present=False),
                       "6.17.0")
    assert v["verdict"] == "unknown"


def test_classify_ok_restricted():
    v = mod.classify(_state(disabled=1, group=1234), "6.17.0")
    assert v["verdict"] == "ok"


def test_classify_disabled_systemwide():
    v = mod.classify(_state(disabled=2), "6.17.0")
    assert v["verdict"] == "disabled_systemwide"


def test_classify_unrestricted_recent_kernel():
    v = mod.classify(_state(disabled=0, group=-1), "6.17.0")
    assert v["verdict"] == "unrestricted_to_all_users"


def test_classify_kernel_pre_cve():
    v = mod.classify(_state(disabled=0, group=-1), "5.4.0-200")
    assert v["verdict"] == "kernel_pre_cve_fix"


def test_classify_debugfs_locked():
    v = mod.classify(
        _state(disabled=1, group=1234,
                 debugfs_present=True, debugfs_readable=False),
        "6.17.0")
    assert v["verdict"] == "debugfs_locked_requires_root"


def test_classify_priority_disabled_wins():
    v = mod.classify(_state(disabled=2, group=-1,
                                debugfs_present=True,
                                debugfs_readable=False),
                       "5.4.0")
    assert v["verdict"] == "disabled_systemwide"


# --- status integration -----------------------------------------

def test_status_unknown(tmp_path):
    out = mod.status(None,
                       str(tmp_path / "nope1"),
                       str(tmp_path / "nope2"),
                       str(tmp_path / "nope3"),
                       kernel_release="6.17.0-29-generic")
    assert out["ok"] is False
    assert out["verdict"]["verdict"] == "unknown"


def test_status_live_like(tmp_path):
    sd = tmp_path / "sysctl"
    _mk_sysctl(sd, disabled=0, group=-1)
    debugfs = tmp_path / "debugfs"
    debugfs.mkdir(0o700)
    # Make it unreadable to the test (no, root will still read).
    out = mod.status(None,
                       str(sd / "io_uring_disabled"),
                       str(sd / "io_uring_group"),
                       str(debugfs),
                       kernel_release="6.17.0-29-generic")
    assert out["ok"] is True
    assert out["io_uring_disabled"] == 0
    assert out["verdict"]["verdict"] == "unrestricted_to_all_users"
