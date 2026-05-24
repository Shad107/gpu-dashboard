"""Tests for modules/usb_authorized_default_audit.py — R&D #87.1."""
from __future__ import annotations

import pytest

from gpu_dashboard.modules import usb_authorized_default_audit as mod


def _mk_hub(tmp_path, name, *, authorized=1,
              interface_authorized=1):
    d = tmp_path / name
    d.mkdir(parents=True, exist_ok=True)
    (d / "authorized_default").write_text(
        f"{authorized}\n")
    (d / "interface_authorized_default").write_text(
        f"{interface_authorized}\n")


# --- list_root_hubs --------------------------------------------

def test_list_missing(tmp_path):
    assert mod.list_root_hubs(
        str(tmp_path / "nope")) == []


def test_list_skips_non_hubs(tmp_path):
    _mk_hub(tmp_path, "usb1")
    _mk_hub(tmp_path, "usb2")
    (tmp_path / "1-1").mkdir()  # not a root hub
    out = mod.list_root_hubs(str(tmp_path))
    assert out == ["usb1", "usb2"]


# --- read_hub --------------------------------------------------

def test_read_hub(tmp_path):
    _mk_hub(tmp_path, "usb1", authorized=0,
              interface_authorized=1)
    out = mod.read_hub(str(tmp_path), "usb1")
    assert out["authorized_default"] == 0
    assert out["interface_authorized_default"] == 1


# --- detect_usbguard -------------------------------------------

def test_detect_usbguard_absent(tmp_path):
    paths = (str(tmp_path / "nope1"),
              str(tmp_path / "nope2"))
    assert mod.detect_usbguard(paths) is False


def test_detect_usbguard_present(tmp_path):
    d = tmp_path / "usbguard"
    d.mkdir()
    paths = (str(d),)
    assert mod.detect_usbguard(paths) is True


# --- classify --------------------------------------------------

def test_classify_unknown_no_hubs():
    v = mod.classify([], usbguard_present=False)
    assert v["verdict"] == "unknown"


def _hub(name="usb1", authorized=1, interface=1):
    return {"name": name,
              "authorized_default": authorized,
              "interface_authorized_default": interface}


def test_classify_usbguard_overrides_to_ok():
    # USBGuard present overrides any other state
    v = mod.classify([_hub()], usbguard_present=True)
    assert v["verdict"] == "ok"


def test_classify_all_gated():
    v = mod.classify([_hub(authorized=0)],
                          usbguard_present=False)
    assert v["verdict"] == "ok"


def test_classify_interface_gated():
    # interface_authorized_default=0 alone is sufficient
    v = mod.classify([_hub(authorized=1, interface=0)],
                          usbguard_present=False)
    assert v["verdict"] == "ok"


def test_classify_all_open_no_guard():
    v = mod.classify(
        [_hub(authorized=1, interface=1)] * 8,
        usbguard_present=False)
    assert v["verdict"] == "usb_default_authorized_no_guard"


def test_classify_mixed():
    v = mod.classify([
        _hub(name="usb1", authorized=1, interface=1),
        _hub(name="usb2", authorized=0, interface=1),
    ], usbguard_present=False)
    assert v["verdict"] == "usb_mixed_authorization"


# Priority : usbguard > all_gated > mixed > all_open
def test_priority_usbguard_over_mixed():
    v = mod.classify([
        _hub(name="usb1", authorized=1, interface=1),
        _hub(name="usb2", authorized=0, interface=1),
    ], usbguard_present=True)
    assert v["verdict"] == "ok"


# --- status integration ----------------------------------------

def test_status_unknown(tmp_path):
    out = mod.status(None, str(tmp_path / "nope"))
    assert out["verdict"]["verdict"] == "unknown"


def test_status_open_no_guard_synthetic(tmp_path):
    _mk_hub(tmp_path, "usb1")
    _mk_hub(tmp_path, "usb2")
    out = mod.status(None, str(tmp_path))
    assert out["hub_count"] == 2
    assert out["usbguard_present"] is False
    assert (out["verdict"]["verdict"]
            == "usb_default_authorized_no_guard")


def test_status_all_gated_synthetic(tmp_path):
    _mk_hub(tmp_path, "usb1", authorized=0)
    _mk_hub(tmp_path, "usb2", authorized=0)
    out = mod.status(None, str(tmp_path))
    assert out["verdict"]["verdict"] == "ok"
