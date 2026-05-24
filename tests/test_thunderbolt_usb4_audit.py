"""Tests for modules/thunderbolt_usb4_audit.py — R&D #86.2."""
from __future__ import annotations

import pytest

from gpu_dashboard.modules import thunderbolt_usb4_audit as mod


def _mk_domain(tmp_path, idx, *, security="user",
                iommu_dma=1):
    d = tmp_path / f"domain{idx}"
    d.mkdir(parents=True, exist_ok=True)
    (d / "security").write_text(security + "\n")
    (d / "iommu_dma_protection").write_text(
        f"{iommu_dma}\n")


def _mk_device(tmp_path, name, *, authorized=1,
                vendor="Acme", device="Dock"):
    d = tmp_path / name
    d.mkdir(parents=True, exist_ok=True)
    (d / "authorized").write_text(f"{authorized}\n")
    (d / "vendor_name").write_text(vendor + "\n")
    (d / "device_name").write_text(device + "\n")


# --- list_devices ----------------------------------------------

def test_list_missing(tmp_path):
    doms, devs = mod.list_devices(str(tmp_path / "nope"))
    assert doms == [] and devs == []


def test_list_basic(tmp_path):
    _mk_domain(tmp_path, 0)
    _mk_domain(tmp_path, 1)
    _mk_device(tmp_path, "0-1")
    _mk_device(tmp_path, "1-3")
    (tmp_path / "control").mkdir()  # ignored
    doms, devs = mod.list_devices(str(tmp_path))
    assert doms == ["domain0", "domain1"]
    assert devs == ["0-1", "1-3"]


# --- read_domain / read_device ---------------------------------

def test_read_domain(tmp_path):
    _mk_domain(tmp_path, 0, security="secure", iommu_dma=1)
    out = mod.read_domain(str(tmp_path), "domain0")
    assert out["security"] == "secure"
    assert out["iommu_dma_protection"] == 1


def test_read_device(tmp_path):
    _mk_device(tmp_path, "0-1", authorized=0,
                  vendor="Apple", device="Studio Display")
    out = mod.read_device(str(tmp_path), "0-1")
    assert out["authorized"] == 0
    assert out["vendor_name"] == "Apple"


# --- classify --------------------------------------------------

def test_classify_na_no_bus():
    v = mod.classify([], [], bus_present=False)
    assert v["verdict"] == "n/a"


def test_classify_na_empty_bus():
    v = mod.classify([], [], bus_present=True)
    assert v["verdict"] == "n/a"


def _dom(name="domain0", security="secure", iommu=1):
    return {"name": name, "security": security,
              "iommu_dma_protection": iommu}


def _dev(name="0-1", authorized=1,
          vendor="Acme", device="Dock", iommu=1):
    return {"name": name, "authorized": authorized,
              "vendor_name": vendor, "device_name": device,
              "iommu_dma_protection": iommu}


def test_classify_ok():
    v = mod.classify([_dom()], [_dev()],
                          bus_present=True)
    assert v["verdict"] == "ok"


def test_classify_unauthenticated():
    v = mod.classify(
        [_dom(security="user")],
        [_dev(authorized=0)],
        bus_present=True)
    assert v["verdict"] == "unauthenticated_device"


def test_classify_unauthenticated_on_dponly_ok():
    # dponly mode doesn't require authorization
    v = mod.classify(
        [_dom(security="dponly")],
        [_dev(authorized=0)],
        bus_present=True)
    assert v["verdict"] == "ok"


def test_classify_security_none_with_device():
    v = mod.classify(
        [_dom(security="none")],
        [_dev()],
        bus_present=True)
    assert v["verdict"] == "security_none"


def test_classify_security_none_no_device_ok():
    # security=none on a domain with NO devices is benign
    # (no DMA gate matters if nothing's attached)
    v = mod.classify(
        [_dom(security="none")],
        [],
        bus_present=True)
    assert v["verdict"] == "ok"


def test_classify_no_iommu_dma_protection():
    v = mod.classify(
        [_dom(iommu=0)],
        [_dev()],
        bus_present=True)
    assert v["verdict"] == "no_iommu_dma_protection"


# Priority : unauthenticated > security_none > no_iommu
def test_priority_unauth_over_security_none():
    # Two domains, one with each issue
    v = mod.classify(
        [_dom(name="domain0", security="user"),
         _dom(name="domain1", security="none")],
        [_dev(name="0-1", authorized=0),
         _dev(name="1-1", authorized=1)],
        bus_present=True)
    assert v["verdict"] == "unauthenticated_device"


def test_priority_security_none_over_iommu():
    v = mod.classify(
        [_dom(security="none", iommu=0)],
        [_dev()],
        bus_present=True)
    assert v["verdict"] == "security_none"


# --- status integration ----------------------------------------

def test_status_na(tmp_path):
    out = mod.status(None, str(tmp_path / "nope"))
    assert out["verdict"]["verdict"] == "n/a"


def test_status_ok_synthetic(tmp_path):
    _mk_domain(tmp_path, 0, security="secure")
    _mk_device(tmp_path, "0-1")
    out = mod.status(None, str(tmp_path))
    assert out["bus_present"] is True
    assert out["domain_count"] == 1
    assert out["device_count"] == 1
    assert out["verdict"]["verdict"] == "ok"


def test_status_unauthenticated_synthetic(tmp_path):
    _mk_domain(tmp_path, 0, security="user")
    _mk_device(tmp_path, "0-1", authorized=0)
    out = mod.status(None, str(tmp_path))
    assert out["ok"] is False
    assert (out["verdict"]["verdict"]
            == "unauthenticated_device")
