"""Tests for modules/i2c_smbus_audit.py — R&D #52.2."""
from __future__ import annotations

import os
import pytest

from gpu_dashboard.modules import i2c_smbus_audit as mod


# --- _is_world_writable -----------------------------------------

def test_is_world_writable():
    assert mod._is_world_writable(0o666) is True
    assert mod._is_world_writable(0o622) is True
    assert mod._is_world_writable(0o660) is False
    assert mod._is_world_writable(0o600) is False


# --- list_adapters ----------------------------------------------

def _mk_adapter(root, idx, name, driver=None):
    d = root / f"i2c-{idx}"
    d.mkdir(parents=True, exist_ok=True)
    (d / "name").write_text(name + "\n")
    if driver is not None:
        drv_target = root.parent / "drivers" / driver
        drv_target.mkdir(parents=True, exist_ok=True)
        (d / "driver").symlink_to(drv_target)


def test_list_adapters_missing(tmp_path):
    assert mod.list_adapters(str(tmp_path / "nope")) == []


def test_list_adapters_empty(tmp_path):
    assert mod.list_adapters(str(tmp_path)) == []


def test_list_adapters_basic(tmp_path):
    _mk_adapter(tmp_path, 0, "SMBus I801 adapter at 0000:00:1f.3",
                  driver="i2c_i801")
    _mk_adapter(tmp_path, 1, "NVIDIA i2c adapter 1 at 1:00.0")
    out = mod.list_adapters(str(tmp_path))
    assert len(out) == 2
    assert out[0]["name"].startswith("SMBus")
    assert out[0]["driver"] == "i2c_i801"
    assert out[1]["name"].startswith("NVIDIA")
    assert out[1]["driver"] is None


def test_list_adapters_ignores_other(tmp_path):
    _mk_adapter(tmp_path, 0, "X")
    (tmp_path / "platform").mkdir()
    out = mod.list_adapters(str(tmp_path))
    assert len(out) == 1


# --- list_nvidia_display ----------------------------------------

def _mk_pci(root, bdf, vendor, klass):
    d = root / bdf
    d.mkdir(parents=True, exist_ok=True)
    (d / "vendor").write_text(vendor + "\n")
    (d / "class").write_text(klass + "\n")


def test_list_nvidia_display(tmp_path):
    _mk_pci(tmp_path, "0000:01:00.0", "0x10de", "0x030000")
    _mk_pci(tmp_path, "0000:01:00.1", "0x10de", "0x040300")  # audio
    _mk_pci(tmp_path, "0000:02:00.0", "0x8086", "0x030000")  # intel iGPU
    out = mod.list_nvidia_display(str(tmp_path))
    assert out == ["0000:01:00.0"]


def test_list_nvidia_display_missing(tmp_path):
    assert mod.list_nvidia_display(str(tmp_path / "nope")) == []


# --- list_dev_nodes ---------------------------------------------

def test_list_dev_nodes(tmp_path):
    # Create regular files in place of char devices; stat is enough.
    (tmp_path / "i2c-0").write_text("")
    os.chmod(tmp_path / "i2c-0", 0o660)
    (tmp_path / "i2c-1").write_text("")
    os.chmod(tmp_path / "i2c-1", 0o666)
    (tmp_path / "other").write_text("")
    out = mod.list_dev_nodes(str(tmp_path))
    assert len(out) == 2
    assert out[0]["mode"] == 0o660
    assert out[1]["mode"] == 0o666


# --- classify ---------------------------------------------------

def _node(name="i2c-0", mode=0o660):
    return {"name": name, "mode": mode, "uid": 0, "gid": 0}


def _ad(idx=0, name="SMBus I801 adapter at 0000:00:1f.3",
         driver="i2c_i801"):
    return {"id": f"i2c-{idx}", "name": name, "driver": driver}


def test_classify_unknown():
    v = mod.classify([], [], [], [])
    assert v["verdict"] == "unknown"


def test_classify_ok():
    v = mod.classify([_ad()], [_node()], ["i2c-0"], [])
    assert v["verdict"] == "ok"


def test_classify_world_writable():
    v = mod.classify([_ad()], [_node(mode=0o666)], ["i2c-0"], [])
    assert v["verdict"] == "ddc_bus_world_writable"


def test_classify_i2c_dev_module_absent():
    v = mod.classify([_ad()], [], [], [])
    assert v["verdict"] == "i2c_dev_module_absent"


def test_classify_nvidia_ddc_missing():
    v = mod.classify([_ad()], [_node()], ["i2c-0"],
                       ["0000:01:00.0"])
    assert v["verdict"] == "nvidia_ddc_missing"


def test_classify_nvidia_ddc_present():
    v = mod.classify(
        [_ad(), _ad(idx=1, name="NVIDIA i2c adapter 1 at 1:00.0",
                     driver=None)],
        [_node(), _node(name="i2c-1")],
        ["i2c-0", "i2c-1"],
        ["0000:01:00.0"])
    # NVIDIA adapter present + nvidia_display present → not the
    # ddc_missing branch ; orphan adapter triggers instead.
    assert v["verdict"] == "smbus_orphan_adapter"


def test_classify_orphan():
    v = mod.classify([_ad(driver=None)], [_node()], ["i2c-0"], [])
    assert v["verdict"] == "smbus_orphan_adapter"


def test_classify_priority_writable_wins_over_orphan():
    v = mod.classify([_ad(driver=None)], [_node(mode=0o666)],
                       ["i2c-0"], [])
    assert v["verdict"] == "ddc_bus_world_writable"


# --- status integration -----------------------------------------

def test_status_empty(tmp_path):
    out = mod.status(None,
                       str(tmp_path / "nai2c"),
                       str(tmp_path / "naclass"),
                       str(tmp_path / "napci"),
                       str(tmp_path / "nadev"))
    assert out["ok"] is False
    assert out["verdict"]["verdict"] == "unknown"


def test_status_ok(tmp_path):
    sysd = tmp_path / "i2c"
    devd = tmp_path / "dev"
    devd.mkdir()
    _mk_adapter(sysd, 0, "SMBus I801", driver="i2c_i801")
    (devd / "i2c-0").write_text("")
    os.chmod(devd / "i2c-0", 0o660)
    classd = tmp_path / "i2cdev"
    classd.mkdir()
    (classd / "i2c-0").mkdir()
    out = mod.status(None, str(sysd), str(classd),
                       str(tmp_path / "napci"), str(devd))
    assert out["ok"] is True
    assert out["adapter_count"] == 1
    assert out["verdict"]["verdict"] == "ok"
