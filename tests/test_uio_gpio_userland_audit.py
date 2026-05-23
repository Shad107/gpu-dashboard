"""Tests for modules/uio_gpio_userland_audit.py — R&D #70.3."""
from __future__ import annotations

import os

import pytest

from gpu_dashboard.modules import uio_gpio_userland_audit as mod


def _mk_uio(root, name, *, drv_name="custom-uio",
                version="1.0"):
    d = root / name
    d.mkdir(parents=True, exist_ok=True)
    (d / "name").write_text(drv_name + "\n")
    (d / "version").write_text(version + "\n")


def _mk_gpio_chip(root, name, *, label="gpio-pl061",
                       base=0, ngpio=8):
    d = root / name
    d.mkdir(parents=True, exist_ok=True)
    (d / "label").write_text(label + "\n")
    (d / "base").write_text(f"{base}\n")
    (d / "ngpio").write_text(f"{ngpio}\n")


def _mk_gpio_pin(root, pin, *, value="0", direction="in"):
    d = root / f"gpio{pin}"
    d.mkdir(parents=True, exist_ok=True)
    if value is not None:
        (d / "value").write_text(value + "\n")
    if direction is not None:
        (d / "direction").write_text(direction + "\n")


# --- list_uio_devices ------------------------------------------

def test_list_uio_missing(tmp_path):
    out = mod.list_uio_devices(str(tmp_path / "nope"),
                                       str(tmp_path / "no_dev"))
    assert out == []


def test_list_uio(tmp_path):
    _mk_uio(tmp_path, "uio0", drv_name="custom")
    _mk_uio(tmp_path, "uio1", drv_name="fpga")
    out = mod.list_uio_devices(str(tmp_path),
                                       str(tmp_path / "dev_uio"))
    assert len(out) == 2
    names = sorted(u["name"] for u in out)
    assert names == ["custom", "fpga"]


def test_list_uio_with_dev_node(tmp_path):
    sysdir = tmp_path / "sysuio"; sysdir.mkdir()
    _mk_uio(sysdir, "uio0")
    dev_dir = tmp_path / "dev"; dev_dir.mkdir()
    dev_node = dev_dir / "uio0"
    dev_node.write_text("")
    os.chmod(str(dev_node), 0o666)
    out = mod.list_uio_devices(str(sysdir),
                                       str(dev_dir / "uio"))
    assert out[0]["dev_node_present"] is True
    assert out[0]["dev_node_mode"] == 0o666


# --- list_gpio_state -------------------------------------------

def test_list_gpio_missing(tmp_path):
    out = mod.list_gpio_state(str(tmp_path / "nope"))
    assert out == {"legacy_pins": [], "chips": [],
                      "export_present": False}


def test_list_gpio_chips_only(tmp_path):
    (tmp_path / "export").write_text("")
    _mk_gpio_chip(tmp_path, "gpiochip0", label="root-chip",
                       base=0, ngpio=8)
    out = mod.list_gpio_state(str(tmp_path))
    assert out["export_present"] is True
    assert len(out["chips"]) == 1
    assert out["chips"][0]["label"] == "root-chip"
    assert out["legacy_pins"] == []


def test_list_gpio_legacy_pins(tmp_path):
    _mk_gpio_pin(tmp_path, 17, value="1", direction="out")
    _mk_gpio_pin(tmp_path, 23, value="0", direction="in")
    out = mod.list_gpio_state(str(tmp_path))
    assert len(out["legacy_pins"]) == 2
    pins = {p["pin"]: p for p in out["legacy_pins"]}
    assert pins[17]["value"] == "1"


# --- classify ---------------------------------------------------

def test_classify_unknown():
    v = mod.classify([], {"legacy_pins": [], "chips": [],
                              "export_present": False},
                          False, False)
    assert v["verdict"] == "unknown"


def test_classify_uio_world_writable():
    v = mod.classify(
        [{"id": "uio0", "name": "fpga", "version": "1",
            "dev_node_present": True,
            "dev_node_mode": 0o666}],
        {"legacy_pins": [], "chips": [],
          "export_present": True},
        True, True)
    assert v["verdict"] == "uio_world_writable"


def test_classify_orphan_gpio():
    v = mod.classify([],
                          {"legacy_pins":
                              [{"pin": 17,
                                  "value": None,
                                  "direction": None}],
                            "chips": [],
                            "export_present": True},
                          False, True)
    assert v["verdict"] == "orphan_gpio_exported"


def test_classify_legacy_gpio_in_use():
    v = mod.classify([],
                          {"legacy_pins":
                              [{"pin": 17, "value": "1",
                                  "direction": "out"}],
                            "chips": [], "export_present": True},
                          False, True)
    assert v["verdict"] == "legacy_gpio_sysfs_in_use"


def test_classify_uio_unowned():
    v = mod.classify(
        [{"id": "uio0", "name": None, "version": None,
            "dev_node_present": False,
            "dev_node_mode": None}],
        {"legacy_pins": [], "chips": [],
          "export_present": False},
        True, False)
    assert v["verdict"] == "uio_present_unowned"


def test_classify_ok():
    v = mod.classify(
        [{"id": "uio0", "name": "fpga", "version": "1",
            "dev_node_present": True,
            "dev_node_mode": 0o660}],
        {"legacy_pins": [], "chips":
            [{"id": "gpiochip0", "label": "x",
                "base": "0", "ngpio": "8"}],
          "export_present": True},
        True, True)
    assert v["verdict"] == "ok"


# Priority : ww > orphan > legacy > unowned.
def test_priority_ww_over_orphan():
    v = mod.classify(
        [{"id": "uio0", "name": "fpga", "version": "1",
            "dev_node_present": True,
            "dev_node_mode": 0o666}],
        {"legacy_pins":
            [{"pin": 17, "value": None,
                "direction": None}],
          "chips": [], "export_present": True},
        True, True)
    assert v["verdict"] == "uio_world_writable"


def test_priority_orphan_over_legacy():
    v = mod.classify([],
                          {"legacy_pins":
                              [{"pin": 17,
                                  "value": None,
                                  "direction": None},
                                {"pin": 18, "value": "0",
                                  "direction": "in"}],
                            "chips": [],
                            "export_present": True},
                          False, True)
    assert v["verdict"] == "orphan_gpio_exported"


# --- status integration -----------------------------------------

def test_status_unknown(tmp_path):
    out = mod.status(None,
                          str(tmp_path / "no_uio"),
                          str(tmp_path / "no_gpio"))
    assert out["ok"] is False
    assert out["verdict"]["verdict"] == "unknown"


def test_status_ok_synthetic(tmp_path):
    gpio = tmp_path / "gpio"; gpio.mkdir()
    (gpio / "export").write_text("")
    _mk_gpio_chip(gpio, "gpiochip0")
    out = mod.status(None,
                          str(tmp_path / "no_uio"),
                          str(gpio))
    assert out["ok"] is True
    assert out["gpio_chip_count"] == 1
    assert out["verdict"]["verdict"] == "ok"


def test_status_legacy_pin_synthetic(tmp_path):
    gpio = tmp_path / "gpio"; gpio.mkdir()
    _mk_gpio_pin(gpio, 17, value="1", direction="out")
    out = mod.status(None,
                          str(tmp_path / "no_uio"),
                          str(gpio))
    assert out["verdict"]["verdict"] == "legacy_gpio_sysfs_in_use"
