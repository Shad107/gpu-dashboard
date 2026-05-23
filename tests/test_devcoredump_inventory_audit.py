"""Tests for modules/devcoredump_inventory_audit.py — R&D #70.4."""
from __future__ import annotations

import os

import pytest

from gpu_dashboard.modules import devcoredump_inventory_audit as mod


def _mk_devcd(root, name, *, failing_device="/sys/devices/foo",
                  data_size=4096, disabled=0):
    d = root / name
    d.mkdir(parents=True, exist_ok=True)
    (d / "failing_device").write_text(failing_device + "\n")
    (d / "data").write_bytes(b"x" * data_size)
    (d / "disabled").write_text(f"{disabled}\n")


# --- list_pending_dumps ----------------------------------------

def test_list_missing(tmp_path):
    assert mod.list_pending_dumps(str(tmp_path / "nope")) == []


def test_list_empty(tmp_path):
    (tmp_path / "disabled").write_text("0\n")
    assert mod.list_pending_dumps(str(tmp_path)) == []


def test_list_gpu_dump(tmp_path):
    _mk_devcd(tmp_path, "devcd0",
                  failing_device="/sys/devices/pci0/0000:01:00.0/"
                                       "drm/card0")
    (tmp_path / "disabled").write_text("0\n")
    out = mod.list_pending_dumps(str(tmp_path))
    assert len(out) == 1
    assert out[0]["id"] == "devcd0"
    assert out[0]["data_size"] == 4096
    # 'drm/card0' alone doesn't match the GPU driver regex —
    # but a path that includes 'amdgpu' / 'nvidia' / 'i915' will.
    assert out[0]["is_gpu"] is False


def test_list_gpu_dump_amdgpu(tmp_path):
    _mk_devcd(tmp_path, "devcd0",
                  failing_device="/sys/devices/.../amdgpu/0000:01")
    out = mod.list_pending_dumps(str(tmp_path))
    assert out[0]["is_gpu"] is True


# --- per_driver_opt_outs ----------------------------------------

def test_per_driver_opt_outs(tmp_path):
    p = (tmp_path / "i915" / "parameters")
    p.mkdir(parents=True, exist_ok=True)
    (p / "disable_devcoredump").write_text("Y\n")
    out = mod.per_driver_opt_outs(str(tmp_path))
    assert out == [{"module": "i915", "disabled": "Y"}]


# --- classify ---------------------------------------------------

def test_classify_capability_missing():
    v = mod.classify([], None, False)
    assert v["verdict"] == "devcoredump_capability_missing"


def test_classify_gpu_dump():
    v = mod.classify(
        [{"id": "devcd0", "failing_device":
            "/sys/devices/.../amdgpu/0000:01",
            "data_present": True, "data_size": 4096,
            "disabled": 0, "is_gpu": True}],
        0, True)
    assert v["verdict"] == "gpu_devcoredump_present"


def test_classify_non_gpu_dump():
    v = mod.classify(
        [{"id": "devcd0", "failing_device":
            "/sys/devices/.../wifi0",
            "data_present": True, "data_size": 1024,
            "disabled": 0, "is_gpu": False}],
        0, True)
    assert v["verdict"] == "recent_devcoredump_pending"


def test_classify_global_disabled():
    v = mod.classify([], 1, True)
    assert v["verdict"] == "devcoredump_disabled_globally"


def test_classify_ok():
    v = mod.classify([], 0, True)
    assert v["verdict"] == "ok"


def test_classify_no_disabled_field_ok():
    # Some kernels don't expose /sys/class/devcoredump/disabled
    v = mod.classify([], None, True)
    assert v["verdict"] == "ok"


# Priority : gpu > non-gpu > global_disabled
def test_priority_gpu_over_non_gpu():
    v = mod.classify(
        [{"id": "devcd0", "failing_device":
            "/sys/devices/.../wifi0",
            "data_present": True, "data_size": 1024,
            "disabled": 0, "is_gpu": False},
          {"id": "devcd1", "failing_device":
            "/sys/devices/.../nvidia/0000:01",
            "data_present": True, "data_size": 8192,
            "disabled": 0, "is_gpu": True}],
        0, True)
    assert v["verdict"] == "gpu_devcoredump_present"


def test_priority_pending_over_global_disabled():
    v = mod.classify(
        [{"id": "devcd0", "failing_device":
            "/sys/devices/.../wifi0",
            "data_present": True, "data_size": 1024,
            "disabled": 0, "is_gpu": False}],
        1, True)
    assert v["verdict"] == "recent_devcoredump_pending"


# --- status integration -----------------------------------------

def test_status_capability_missing(tmp_path):
    out = mod.status(None, str(tmp_path / "nope"),
                          str(tmp_path / "no_mod"))
    assert out["ok"] is False
    assert out["verdict"]["verdict"] == \
        "devcoredump_capability_missing"


def test_status_ok_synthetic(tmp_path):
    devcd = tmp_path / "devcd"; devcd.mkdir()
    (devcd / "disabled").write_text("0\n")
    out = mod.status(None, str(devcd),
                          str(tmp_path / "no_mod"))
    assert out["ok"] is True
    assert out["global_disabled"] == 0
    assert out["pending_count"] == 0
    assert out["verdict"]["verdict"] == "ok"


def test_status_global_disabled_synthetic(tmp_path):
    devcd = tmp_path / "devcd"; devcd.mkdir()
    (devcd / "disabled").write_text("1\n")
    out = mod.status(None, str(devcd),
                          str(tmp_path / "no_mod"))
    assert out["verdict"]["verdict"] == \
        "devcoredump_disabled_globally"
