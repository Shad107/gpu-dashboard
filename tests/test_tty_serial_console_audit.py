"""Tests for modules/tty_serial_console_audit.py — R&D #84.4."""
from __future__ import annotations

import pytest

from gpu_dashboard.modules import tty_serial_console_audit as mod


def _mk_console_active(tmp_path, value):
    d = tmp_path / "console"
    d.mkdir(parents=True, exist_ok=True)
    (d / "active").write_text(value + "\n")


def _mk_usb_serial(tmp_path, name, runtime_status="active"):
    d = tmp_path / name / "device" / "power"
    d.mkdir(parents=True, exist_ok=True)
    (d / "runtime_status").write_text(runtime_status + "\n")


# --- read_active_consoles --------------------------------------

def test_read_active_consoles_missing(tmp_path):
    assert mod.read_active_consoles(
        str(tmp_path / "nope")) == []


def test_read_active_consoles_tty0(tmp_path):
    _mk_console_active(tmp_path, "tty0")
    assert mod.read_active_consoles(str(tmp_path)) == [
        "tty0"]


def test_read_active_consoles_multiple(tmp_path):
    _mk_console_active(tmp_path, "tty0 ttyS0")
    assert mod.read_active_consoles(str(tmp_path)) == [
        "tty0", "ttyS0"]


# --- list_serial_devices ---------------------------------------

def test_list_serial_missing(tmp_path):
    assert mod.list_serial_devices(
        str(tmp_path / "nope")) == []


def test_list_serial_skips_non_usb(tmp_path):
    # tty0, ttyS0 should be skipped
    (tmp_path / "tty0").mkdir()
    (tmp_path / "ttyS0").mkdir()
    assert mod.list_serial_devices(str(tmp_path)) == []


def test_list_serial_ttyusb(tmp_path):
    _mk_usb_serial(tmp_path, "ttyUSB0",
                     runtime_status="active")
    _mk_usb_serial(tmp_path, "ttyACM0",
                     runtime_status="suspended")
    out = mod.list_serial_devices(str(tmp_path))
    assert len(out) == 2
    by_name = {d["name"]: d for d in out}
    assert by_name["ttyUSB0"]["runtime_status"] == "active"
    assert (by_name["ttyACM0"]["runtime_status"]
            == "suspended")


# --- classify --------------------------------------------------

def test_classify_na():
    v = mod.classify([], [], False)
    assert v["verdict"] == "n/a"


def test_classify_ok_tty0():
    v = mod.classify(["tty0"], [], True)
    assert v["verdict"] == "ok"


def test_classify_serial_console_no_cable():
    v = mod.classify(["ttyS0"], [], True)
    assert v["verdict"] == "serial_console_no_cable"


def test_classify_dual_console_ok():
    # Both tty0 and ttyS0 = informational, not flagged
    v = mod.classify(["tty0", "ttyS0"], [], True)
    assert v["verdict"] == "ok"


def test_classify_usb_serial_error():
    v = mod.classify(["tty0"], [
        {"name": "ttyUSB0", "runtime_status": "error"},
    ], True)
    assert v["verdict"] == "usb_serial_error_state"


def test_classify_usb_serial_unstable_names():
    v = mod.classify(["tty0"], [
        {"name": "ttyUSB0", "runtime_status": "active"},
        {"name": "ttyUSB1", "runtime_status": "active"},
    ], True)
    assert v["verdict"] == "usb_serial_unstable_names"


def test_classify_single_usb_serial_ok():
    v = mod.classify(["tty0"], [
        {"name": "ttyUSB0", "runtime_status": "active"},
    ], True)
    assert v["verdict"] == "ok"


# Priority : serial_console > usb_error > unstable_names
def test_priority_serial_console_over_usb_error():
    v = mod.classify(["ttyS0"], [
        {"name": "ttyUSB0", "runtime_status": "error"},
    ], True)
    assert v["verdict"] == "serial_console_no_cable"


def test_priority_usb_error_over_unstable():
    v = mod.classify(["tty0"], [
        {"name": "ttyUSB0", "runtime_status": "error"},
        {"name": "ttyUSB1", "runtime_status": "active"},
    ], True)
    assert v["verdict"] == "usb_serial_error_state"


# --- status integration ----------------------------------------

def test_status_na(tmp_path):
    out = mod.status(None, str(tmp_path / "nope"))
    assert out["verdict"]["verdict"] == "n/a"


def test_status_ok_synthetic(tmp_path):
    _mk_console_active(tmp_path, "tty0")
    out = mod.status(None, str(tmp_path))
    assert out["ok"] is True
    assert out["consoles"] == ["tty0"]
    assert out["verdict"]["verdict"] == "ok"


def test_status_serial_console_synthetic(tmp_path):
    _mk_console_active(tmp_path, "ttyS0")
    out = mod.status(None, str(tmp_path))
    assert out["ok"] is False
    assert (out["verdict"]["verdict"]
            == "serial_console_no_cable")
