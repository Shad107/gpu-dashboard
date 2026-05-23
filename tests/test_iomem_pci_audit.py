"""Tests for modules/iomem_pci_audit.py — R&D #51.2."""
from __future__ import annotations

import pytest

from gpu_dashboard.modules import iomem_pci_audit as mod


IOMEM_REAL = """\
00000000-00000fff : Reserved
00001000-0009ffff : System RAM
000a0000-000bffff : PCI Bus 0000:00
000c0000-000dffff : PCI Bus 0000:00
000e0000-000fffff : System ROM
00100000-bfedffff : System RAM
  1f000000-1fffffff : Crash kernel
"""

IOMEM_MASKED = """\
00000000-00000000 : Reserved
00000000-00000000 : System RAM
00000000-00000000 : PCI Bus 0000:00
"""


# --- parse_iomem -------------------------------------------------

def test_parse_iomem_real():
    out = mod.parse_iomem(IOMEM_REAL)
    assert out["region_count"] == 7
    assert out["masked"] is False
    labels = [l["label"] for l in out["top_labels"]]
    assert "Reserved" in labels
    assert "System RAM" in labels


def test_parse_iomem_masked():
    out = mod.parse_iomem(IOMEM_MASKED)
    assert out["masked"] is True


def test_parse_iomem_empty():
    assert mod.parse_iomem("") == {"region_count": 0,
                                       "top_labels": [],
                                       "masked": False}


# --- is_host_bridge ----------------------------------------------

def test_is_host_bridge():
    # base class 0x06 = bridge, subclass 0x00 = host bridge
    assert mod.is_host_bridge(0x060000) is True
    assert mod.is_host_bridge(0x060400) is True  # PCI-to-PCI bridge
    assert mod.is_host_bridge(0x030000) is False  # display controller


def test_is_host_bridge_none():
    assert mod.is_host_bridge(None) is False


# --- list_pci_devices --------------------------------------------

def _mk_dev(root, bdf, *, class_hex="0x030000", driver=None,
              reset_method="flr bus", numa=-1, vendor="0x10de",
              device="0x2204"):
    d = root / bdf
    d.mkdir(parents=True, exist_ok=True)
    (d / "vendor").write_text(vendor + "\n")
    (d / "device").write_text(device + "\n")
    (d / "class").write_text(class_hex + "\n")
    (d / "reset_method").write_text(reset_method + "\n")
    (d / "numa_node").write_text(str(numa) + "\n")
    (d / "enable").write_text("1\n")
    if driver is not None:
        # Make driver symlink
        drv = root.parent / "drivers" / driver
        drv.mkdir(parents=True, exist_ok=True)
        (d / "driver").symlink_to(drv)


def test_list_pci_devices(tmp_path):
    sysd = tmp_path / "devices"
    _mk_dev(sysd, "0000:01:00.0", driver="nvidia")
    _mk_dev(sysd, "0000:02:00.0", class_hex="0x040300", driver=None)
    out = mod.list_pci_devices(str(sysd))
    assert len(out) == 2
    nv = next(d for d in out if d["bdf"] == "0000:01:00.0")
    assert nv["driver"] == "nvidia"
    assert nv["class"] == 0x030000


def test_list_pci_devices_class_bare_hex(tmp_path):
    # /sys/bus/pci/devices/<bdf>/class uses 0x prefix but int(s, 0)
    # in _read_int handles it. Make sure parsing works.
    sysd = tmp_path / "devices"
    _mk_dev(sysd, "0000:00:00.0", class_hex="0x060000")
    out = mod.list_pci_devices(str(sysd))
    assert out[0]["class"] == 0x060000


def test_list_pci_devices_missing(tmp_path):
    assert mod.list_pci_devices(str(tmp_path / "nope")) == []


# --- classify ----------------------------------------------------

def _dev(bdf="0000:01:00.0", class_=0x030000, driver="nvidia",
          reset_method="flr bus"):
    return {"bdf": bdf, "vendor": "0x10de", "device": "0x2204",
              "class": class_, "driver": driver,
              "reset_method": reset_method, "numa_node": -1, "enable": 1}


def test_classify_unknown():
    v = mod.classify({"region_count": 0}, [])
    assert v["verdict"] == "unknown"


def test_classify_ok():
    v = mod.classify({"region_count": 100, "masked": False},
                       [_dev()])
    assert v["verdict"] == "ok"


def test_classify_unbound_device():
    v = mod.classify({"region_count": 100, "masked": False},
                       [_dev(driver=None)])
    assert v["verdict"] == "unbound_device"


def test_classify_unbound_skips_host_bridge():
    # Host bridge with no driver shouldn't be flagged.
    v = mod.classify({"region_count": 100, "masked": False},
                       [_dev(class_=0x060000, driver=None)])
    assert v["verdict"] == "ok"


def test_classify_reset_method_bus_only():
    v = mod.classify({"region_count": 100, "masked": False},
                       [_dev(reset_method="bus")])
    assert v["verdict"] == "reset_method_bus_only"


def test_classify_iomem_masked():
    v = mod.classify({"region_count": 100, "masked": True},
                       [_dev()])
    assert v["verdict"] == "iomem_masked"


def test_classify_priority_unbound_wins():
    v = mod.classify({"region_count": 100, "masked": True},
                       [_dev(driver=None, reset_method="bus")])
    assert v["verdict"] == "unbound_device"


# --- status integration ------------------------------------------

def test_status_unknown(monkeypatch, tmp_path):
    monkeypatch.setattr(mod, "_PROC_IOMEM",
                        str(tmp_path / "nope"))
    monkeypatch.setattr(mod, "_SYS_BUS_PCI",
                        str(tmp_path / "nopci"))
    out = mod.status()
    assert out["ok"] is False
    assert out["verdict"]["verdict"] == "unknown"
