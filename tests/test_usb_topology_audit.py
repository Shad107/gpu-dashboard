"""Tests for modules/usb_topology_audit.py — R&D #48.2."""
from __future__ import annotations

from pathlib import Path

import pytest

from gpu_dashboard.modules import usb_topology_audit as mod


def _mk_dev(root: Path, name: str, *, idVendor="0627",
             idProduct="0001", manufacturer="QEMU", product="Tablet",
             speed=480, version=" 2.00", bMaxPower="100mA",
             authorized=1, power_control="auto",
             autosuspend_delay_ms=2000):
    d = root / name
    d.mkdir(parents=True, exist_ok=True)
    (d / "idVendor").write_text(idVendor + "\n")
    (d / "idProduct").write_text(idProduct + "\n")
    (d / "manufacturer").write_text(manufacturer + "\n")
    (d / "product").write_text(product + "\n")
    (d / "speed").write_text(str(speed) + "\n")
    (d / "version").write_text(version + "\n")
    (d / "bMaxPower").write_text(bMaxPower + "\n")
    (d / "authorized").write_text(str(authorized) + "\n")
    (d / "power").mkdir(exist_ok=True)
    (d / "power" / "control").write_text(power_control + "\n")
    (d / "power" / "autosuspend_delay_ms").write_text(
        str(autosuspend_delay_ms) + "\n")


# --- _parse_bMaxPower ---------------------------------------------

def test_parse_bMaxPower():
    assert mod._parse_bMaxPower("500mA\n") == 500
    assert mod._parse_bMaxPower("0mA") == 0
    assert mod._parse_bMaxPower("garbage") is None
    assert mod._parse_bMaxPower("") is None
    assert mod._parse_bMaxPower(None) is None


# --- is_interface_path / is_root_hub ------------------------------

def test_is_interface_path():
    assert mod.is_interface_path("1-1:1.0") is True
    assert mod.is_interface_path("1-1.2:1.0") is True
    assert mod.is_interface_path("1-1") is False
    assert mod.is_interface_path("usb1") is False


def test_is_root_hub():
    assert mod.is_root_hub("usb1") is True
    assert mod.is_root_hub("usb10") is True
    assert mod.is_root_hub("1-1") is False


# --- list_devices -------------------------------------------------

def test_list_devices_skips_interfaces(tmp_path):
    _mk_dev(tmp_path, "usb1", product="UHCI HC")
    _mk_dev(tmp_path, "1-1", product="USB Tablet", speed=480)
    # Interface paths shouldn't be listed.
    (tmp_path / "1-1:1.0").mkdir()
    out = mod.list_devices(str(tmp_path))
    names = {d["name"] for d in out}
    assert "usb1" in names
    assert "1-1" in names
    assert "1-1:1.0" not in names


def test_list_devices_root_hub_flag(tmp_path):
    _mk_dev(tmp_path, "usb1")
    _mk_dev(tmp_path, "1-1")
    out = mod.list_devices(str(tmp_path))
    rh = next(d for d in out if d["name"] == "usb1")
    nrh = next(d for d in out if d["name"] == "1-1")
    assert rh["is_root_hub"] is True
    assert nrh["is_root_hub"] is False


def test_list_devices_missing(tmp_path):
    assert mod.list_devices(str(tmp_path / "nope")) == []


# --- classify ----------------------------------------------------

def _dev(name="1-1", is_root_hub=False, bMaxPower_mA=100,
          speed_mbps=480, version=" 2.00", product="Device",
          manufacturer="X", power_control="auto",
          autosuspend_delay_ms=2000):
    return {"name": name, "is_root_hub": is_root_hub,
              "idVendor": "1234", "idProduct": "5678",
              "manufacturer": manufacturer, "product": product,
              "speed_mbps": speed_mbps, "version": version,
              "bMaxPower_mA": bMaxPower_mA, "authorized": 1,
              "power_control": power_control,
              "autosuspend_delay_ms": autosuspend_delay_ms,
              "bcdDevice": None}


def test_classify_no_devices():
    v = mod.classify([])
    assert v["verdict"] == "no_usb_devices"


def test_classify_ok():
    v = mod.classify([_dev(name="usb1", is_root_hub=True),
                       _dev(name="1-1", bMaxPower_mA=100)])
    assert v["verdict"] == "ok"


def test_classify_power_budget_high():
    devs = [_dev(name="usb1", is_root_hub=True)]
    for i in range(6):
        devs.append(_dev(name=f"1-{i+1}", bMaxPower_mA=100))
    v = mod.classify(devs)
    assert v["verdict"] == "power_budget_high"
    assert "600" in v["reason"]


def test_classify_speed_negotiated_low():
    # USB 3.0 device negotiated 480 Mbps (USB 2.0) — downgraded.
    v = mod.classify([_dev(name="1-1", version=" 3.00",
                              speed_mbps=480,
                              product="USB 3.0 SSD")])
    assert v["verdict"] == "speed_negotiated_low"


def test_classify_speed_ok_when_ss_negotiated():
    # USB 3.0 device negotiated 5000 Mbps — fine.
    v = mod.classify([_dev(name="1-1", version=" 3.00",
                              speed_mbps=5000,
                              product="USB 3.0 SSD")])
    assert v["verdict"] == "ok"


def test_classify_autosuspend_unfriendly():
    v = mod.classify([_dev(name="1-1", product="USB Keyboard",
                              autosuspend_delay_ms=500)])
    assert v["verdict"] == "autosuspend_unfriendly"


def test_classify_autosuspend_skipped_for_non_hid():
    # SSD has short autosuspend — that's fine, not flagged.
    v = mod.classify([_dev(name="1-1", product="USB SSD",
                              autosuspend_delay_ms=500)])
    assert v["verdict"] == "ok"


def test_classify_priority_power_wins():
    devs = [_dev(name="1-1", bMaxPower_mA=600,
                  product="Keyboard",
                  autosuspend_delay_ms=500)]
    v = mod.classify(devs)
    assert v["verdict"] == "power_budget_high"


# --- status integration ------------------------------------------

def test_status_with_isolated(monkeypatch, tmp_path):
    sysbus = tmp_path / "usb"
    _mk_dev(sysbus, "usb1", product="UHCI HC", bMaxPower="0mA")
    _mk_dev(sysbus, "1-1", product="USB Tablet", bMaxPower="100mA")
    monkeypatch.setattr(mod, "_SYS_BUS_USB", str(sysbus))
    out = mod.status()
    assert out["ok"] is True
    assert out["device_count"] == 2
    assert out["non_root_count"] == 1
    assert out["total_power_ma"] == 100
    assert out["verdict"]["verdict"] == "ok"


def test_status_unknown(monkeypatch, tmp_path):
    monkeypatch.setattr(mod, "_SYS_BUS_USB", str(tmp_path / "nope"))
    out = mod.status()
    assert out["ok"] is False
    assert out["verdict"]["verdict"] == "unknown"
