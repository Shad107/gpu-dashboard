"""Tests for modules/wol_ethtool_audit.py — R&D #86.1."""
from __future__ import annotations

import pytest

from gpu_dashboard.modules import wol_ethtool_audit as mod


def _mk_iface(tmp_path, name, *, operstate="up", carrier=1,
                duplex="full", speed=1000, wakeup=""):
    d = tmp_path / name
    d.mkdir(parents=True, exist_ok=True)
    (d / "operstate").write_text(operstate + "\n")
    (d / "carrier").write_text(f"{carrier}\n")
    (d / "duplex").write_text(duplex + "\n")
    (d / "speed").write_text(f"{speed}\n")
    pwr = d / "device" / "power"
    pwr.mkdir(parents=True, exist_ok=True)
    (pwr / "wakeup").write_text(wakeup + "\n")


# --- list_interfaces -------------------------------------------

def test_list_missing(tmp_path):
    assert mod.list_interfaces(
        str(tmp_path / "nope")) == []


def test_list(tmp_path):
    _mk_iface(tmp_path, "eth0")
    _mk_iface(tmp_path, "lo")
    out = mod.list_interfaces(str(tmp_path))
    assert "eth0" in out
    assert "lo" in out


# --- _is_physical ----------------------------------------------

def test_is_physical_eth():
    assert mod._is_physical(
        {"name": "eth0"}) is True


def test_is_physical_skips_lo():
    assert mod._is_physical(
        {"name": "lo"}) is False


def test_is_physical_skips_docker():
    assert mod._is_physical(
        {"name": "docker0"}) is False


def test_is_physical_skips_veth():
    assert mod._is_physical(
        {"name": "veth1234"}) is False


def test_is_physical_skips_compose_bridge():
    assert mod._is_physical(
        {"name": "br-abcdef1234"}) is False


# --- classify --------------------------------------------------

def test_classify_unknown_no_physical():
    v = mod.classify([
        {"name": "lo", "carrier": 0, "speed": -1,
            "duplex": "unknown", "wakeup": "",
            "operstate": "unknown"},
        {"name": "docker0", "carrier": 0, "speed": -1,
            "duplex": "unknown", "wakeup": "",
            "operstate": "down"},
    ])
    assert v["verdict"] == "unknown"


def _iface(name="eth0", operstate="up", carrier=1,
            duplex="full", speed=1000, wakeup=""):
    return {"name": name, "operstate": operstate,
              "carrier": carrier, "duplex": duplex,
              "speed": speed, "wakeup": wakeup}


def test_classify_ok():
    v = mod.classify([_iface()])
    assert v["verdict"] == "ok"


def test_classify_wakeup_armed_no_link():
    v = mod.classify([
        _iface(wakeup="enabled", carrier=0,
                  operstate="down"),
    ])
    assert v["verdict"] == "wakeup_armed_no_link"


def test_classify_wakeup_armed_carrier_only():
    # carrier 0, operstate up (rare but possible during
    # negotiation)
    v = mod.classify([
        _iface(wakeup="enabled", carrier=0,
                  operstate="up"),
    ])
    assert v["verdict"] == "wakeup_armed_no_link"


def test_classify_speed_downshift_half():
    v = mod.classify([
        _iface(speed=100, duplex="half"),
    ])
    assert v["verdict"] == "speed_downshift"


def test_classify_speed_full_100m_ok():
    # 100M full duplex is fine for many home links
    v = mod.classify([
        _iface(speed=100, duplex="full"),
    ])
    assert v["verdict"] == "ok"


def test_classify_virtio_speed_minus_one_ok():
    # virtio NICs report speed = -1 ; not a downshift
    v = mod.classify([
        _iface(speed=-1, duplex="unknown"),
    ])
    assert v["verdict"] == "ok"


def test_classify_wakeup_enabled_healthy():
    v = mod.classify([
        _iface(wakeup="enabled", carrier=1,
                  operstate="up", speed=1000,
                  duplex="full"),
    ])
    assert v["verdict"] == "wakeup_enabled"


# Priority : wakeup_armed > speed_downshift > wakeup_enabled
def test_priority_armed_over_downshift():
    v = mod.classify([
        _iface(name="eth0", wakeup="enabled",
                  carrier=0, operstate="down"),
        _iface(name="eth1", speed=100, duplex="half"),
    ])
    assert v["verdict"] == "wakeup_armed_no_link"


def test_priority_downshift_over_enabled():
    v = mod.classify([
        _iface(name="eth0", speed=100, duplex="half"),
        _iface(name="eth1", wakeup="enabled"),
    ])
    assert v["verdict"] == "speed_downshift"


# --- status integration ----------------------------------------

def test_status_unknown(tmp_path):
    out = mod.status(None, str(tmp_path / "nope"))
    assert out["ok"] is False
    assert out["verdict"]["verdict"] == "unknown"


def test_status_ok_synthetic(tmp_path):
    _mk_iface(tmp_path, "eth0", carrier=1, speed=1000,
                duplex="full")
    out = mod.status(None, str(tmp_path))
    assert out["ok"] is True
    assert out["iface_count"] == 1
    assert out["verdict"]["verdict"] == "ok"


def test_status_wakeup_armed_synthetic(tmp_path):
    _mk_iface(tmp_path, "eth0", wakeup="enabled",
                carrier=0, operstate="down")
    out = mod.status(None, str(tmp_path))
    assert out["ok"] is False
    assert (out["verdict"]["verdict"]
            == "wakeup_armed_no_link")
