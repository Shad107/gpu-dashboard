"""Tests for modules/nvme_hmb_features_audit.py — R&D #91.3."""
from __future__ import annotations

import pytest

from gpu_dashboard.modules import nvme_hmb_features_audit as mod


def _mk_controller(tmp_path, name, *, hmb=None,
                    hmb_unreadable=False):
    d = tmp_path / "nvme" / name
    d.mkdir(parents=True, exist_ok=True)
    if hmb is not None:
        f = d / "hmb"
        f.write_text(str(hmb) + "\n")
        if hmb_unreadable:
            # Simulate via deleting after creation — pytest
            # patches permissions awkwardly, so emulate via
            # a directory at the path.
            f.unlink()
            f.mkdir()
    return str(tmp_path / "nvme")


def _mk_param(tmp_path, value):
    p = tmp_path / "param"
    p.write_text(f"{value}\n")
    return str(p)


def _mk_meminfo(tmp_path, mem_kib=32 * 2**20):
    p = tmp_path / "meminfo"
    p.write_text(f"MemTotal: {mem_kib} kB\n")
    return str(p)


# --- list_controllers ------------------------------------------

def test_list_controllers_missing(tmp_path):
    assert mod.list_controllers(str(tmp_path / "nope")) == []


def test_list_controllers_present(tmp_path):
    r = _mk_controller(tmp_path, "nvme0", hmb=0)
    _mk_controller(tmp_path, "nvme1", hmb=64 * 2**20)
    out = mod.list_controllers(r)
    assert out == ["nvme0", "nvme1"]


def test_list_controllers_skips_non_nvme(tmp_path):
    r = _mk_controller(tmp_path, "nvme0", hmb=0)
    (tmp_path / "nvme" / "weird").mkdir()
    assert mod.list_controllers(r) == ["nvme0"]


# --- read_controller -------------------------------------------

def test_read_controller_no_hmb(tmp_path):
    base = tmp_path / "nvme" / "nvme0"
    base.mkdir(parents=True)
    out = mod.read_controller(
        str(tmp_path / "nvme"), "nvme0")
    assert out["hmb_present"] is False


def test_read_controller_hmb_present(tmp_path):
    _mk_controller(tmp_path, "nvme0", hmb=4 * 2**20)
    out = mod.read_controller(
        str(tmp_path / "nvme"), "nvme0")
    assert out["hmb_present"] is True
    assert out["hmb_bytes"] == 4 * 2**20


# --- classify --------------------------------------------------

def _ctrl(*, name="nvme0", hmb_present=True,
          hmb_readable=True, hmb_bytes=0):
    return {"name": name, "hmb_present": hmb_present,
            "hmb_readable": hmb_readable,
            "hmb_bytes": hmb_bytes}


def test_classify_unknown_no_controllers():
    v = mod.classify([], None, None)
    assert v["verdict"] == "unknown"


def test_classify_requires_root_unreadable():
    v = mod.classify(
        [_ctrl(hmb_present=True, hmb_readable=False,
               hmb_bytes=None)],
        None, None)
    assert v["verdict"] == "requires_root"


def test_classify_ok_no_hmb():
    v = mod.classify(
        [_ctrl(name="nvme0", hmb_bytes=0)],
        None, 32 * 2**30)
    assert v["verdict"] == "ok"


def test_classify_ok_hmb_active_normal():
    v = mod.classify(
        [_ctrl(name="nvme0", hmb_bytes=8 * 2**20)],
        2048, 32 * 2**30)
    assert v["verdict"] == "ok"


def test_classify_hmb_module_off_with_drives():
    v = mod.classify(
        [_ctrl(name="nvme0", hmb_bytes=0)],
        0, 32 * 2**30)
    assert v["verdict"] == "hmb_module_off_with_drives"


def test_classify_hmb_param_disabled_with_use():
    # param off but HMB currently in use → err
    v = mod.classify(
        [_ctrl(name="nvme0", hmb_bytes=8 * 2**20)],
        0, 32 * 2**30)
    assert v["verdict"] == "hmb_param_disabled_with_use"


def test_classify_hmb_oversized_small_ram():
    # HMB total = 128 MiB on 8 GiB box → accent
    v = mod.classify(
        [_ctrl(name="nvme0", hmb_bytes=128 * 2**20)],
        2048, 8 * 2**30)
    assert v["verdict"] == "hmb_oversized"


def test_classify_hmb_large_but_big_ram_is_ok():
    # 128 MiB HMB on 32 GiB → ok
    v = mod.classify(
        [_ctrl(name="nvme0", hmb_bytes=128 * 2**20)],
        2048, 32 * 2**30)
    assert v["verdict"] == "ok"


# Priority : disabled_with_use > module_off > oversized
def test_priority_err_over_warn():
    v = mod.classify(
        [_ctrl(name="nvme0", hmb_bytes=8 * 2**20),
         _ctrl(name="nvme1", hmb_bytes=0)],
        0, 32 * 2**30)
    assert v["verdict"] == "hmb_param_disabled_with_use"


# --- status integration ----------------------------------------

def test_status_unknown(tmp_path):
    out = mod.status(None,
                       str(tmp_path / "nope"),
                       str(tmp_path / "nope_p"),
                       str(tmp_path / "nope_m"))
    assert out["verdict"]["verdict"] == "unknown"


def test_status_ok_synthetic(tmp_path):
    r = _mk_controller(tmp_path, "nvme0", hmb=0)
    p = _mk_param(tmp_path, 2048)
    m = _mk_meminfo(tmp_path)
    out = mod.status(None, r, p, m)
    assert out["verdict"]["verdict"] == "ok"
    assert out["controller_count"] == 1


def test_status_module_off_synthetic(tmp_path):
    r = _mk_controller(tmp_path, "nvme0", hmb=0)
    p = _mk_param(tmp_path, 0)
    m = _mk_meminfo(tmp_path)
    out = mod.status(None, r, p, m)
    assert out["verdict"]["verdict"] == "hmb_module_off_with_drives"
    assert out["max_host_mem_size_mb"] == 0
