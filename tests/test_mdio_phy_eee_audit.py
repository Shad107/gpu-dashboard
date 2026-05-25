"""Tests for modules/mdio_phy_eee_audit.py — R&D #95.1."""
from __future__ import annotations

import pytest

from gpu_dashboard.modules import mdio_phy_eee_audit as mod


def _mk_iface(tmp_path, iface, *, carrier="1",
                phydev_present=True,
                link="1", speed="1000",
                duplex="full",
                eee_enabled="1", eee_active="1"):
    d = tmp_path / "class_net" / iface
    d.mkdir(parents=True, exist_ok=True)
    if carrier is not None:
        (d / "carrier").write_text(carrier + "\n")
    if phydev_present:
        phy = d / "phydev"
        phy.mkdir(exist_ok=True)
        if link is not None:
            (phy / "link").write_text(link + "\n")
        if speed is not None:
            (phy / "speed").write_text(speed + "\n")
        if duplex is not None:
            (phy / "duplex").write_text(duplex + "\n")
        eee = phy / "eee"
        eee.mkdir(exist_ok=True)
        if eee_enabled is not None:
            (eee / "enabled").write_text(eee_enabled + "\n")
        if eee_active is not None:
            (eee / "active").write_text(eee_active + "\n")


# --- read_phydev -----------------------------------------------

def test_read_phydev_missing(tmp_path):
    d = tmp_path / "class_net" / "eth0"
    d.mkdir(parents=True)
    assert mod.read_phydev(str(d)) is None


def test_read_phydev_present(tmp_path):
    _mk_iface(tmp_path, "eth0")
    out = mod.read_phydev(
        str(tmp_path / "class_net" / "eth0"))
    assert out["link"] == 1
    assert out["speed"] == 1000
    assert out["duplex"] == "full"
    assert out["eee_active"] == 1


# --- walk_ifaces -----------------------------------------------

def test_walk_ifaces_missing(tmp_path):
    assert mod.walk_ifaces(str(tmp_path / "nope")) == []


def test_walk_ifaces_skips_no_phydev(tmp_path):
    _mk_iface(tmp_path, "lo")  # excluded by name
    _mk_iface(tmp_path, "ens18", phydev_present=False)
    _mk_iface(tmp_path, "eth0")
    out = mod.walk_ifaces(str(tmp_path / "class_net"))
    assert len(out) == 1
    assert out[0]["iface"] == "eth0"


# --- classify --------------------------------------------------

def _dev(*, iface="eth0", carrier=1, link=1, speed=1000,
         duplex="full", eee_enabled=1, eee_active=1):
    return {"iface": iface, "carrier": carrier, "link": link,
            "speed": speed, "duplex": duplex,
            "eee_enabled": eee_enabled,
            "eee_active": eee_active}


def test_classify_unknown_empty():
    v = mod.classify([])
    assert v["verdict"] == "unknown"


def test_classify_phy_clean():
    v = mod.classify([_dev()])
    assert v["verdict"] == "phy_clean"


def test_classify_phy_no_link_carrier_up_err():
    v = mod.classify([_dev(carrier=1, link=0)])
    assert v["verdict"] == "phy_no_link_carrier_up"


def test_classify_carrier_down_link_down_is_ok():
    # Both down — not a desync
    v = mod.classify([_dev(carrier=0, link=0)])
    assert v["verdict"] == "phy_clean"


def test_classify_eee_active_but_disabled_warn():
    v = mod.classify([_dev(eee_enabled=0, eee_active=1)])
    assert v["verdict"] == "eee_active_but_disabled"


def test_classify_half_duplex_on_gigabit_accent():
    v = mod.classify([_dev(duplex="half", speed=1000)])
    assert v["verdict"] == "duplex_half_on_gbit_phy"


def test_classify_half_duplex_on_100m_is_ok():
    # 100 Mbps half is legacy but plausible
    v = mod.classify([_dev(duplex="half", speed=100)])
    assert v["verdict"] == "phy_clean"


# Priority : no_link > eee > duplex
def test_priority_no_link_over_eee():
    v = mod.classify([_dev(
        carrier=1, link=0,
        eee_enabled=0, eee_active=1)])
    assert v["verdict"] == "phy_no_link_carrier_up"


def test_priority_eee_over_duplex():
    v = mod.classify([_dev(
        eee_enabled=0, eee_active=1,
        duplex="half", speed=1000)])
    assert v["verdict"] == "eee_active_but_disabled"


# --- status integration ----------------------------------------

def test_status_unknown(tmp_path):
    out = mod.status(None, str(tmp_path / "nope"))
    assert out["verdict"]["verdict"] == "unknown"


def test_status_clean_synthetic(tmp_path):
    _mk_iface(tmp_path, "eth0")
    out = mod.status(None, str(tmp_path / "class_net"))
    assert out["verdict"]["verdict"] == "phy_clean"
    assert out["phy_iface_count"] == 1


def test_status_desync_synthetic(tmp_path):
    _mk_iface(tmp_path, "eth0", carrier="1", link="0")
    out = mod.status(None, str(tmp_path / "class_net"))
    assert out["verdict"]["verdict"] == "phy_no_link_carrier_up"
    assert out["ok"] is False
