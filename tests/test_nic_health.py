"""Tests for modules/nic_health.py — R&D #33.1 LAN NIC health correlator."""
from __future__ import annotations

from pathlib import Path

import pytest

from gpu_dashboard.modules import nic_health


def _mk_iface(root: Path, name: str, *, carrier: str = "1",
                  operstate: str = "up", speed: str = "1000",
                  type_: str = "1",
                  rx_bytes: int = 0, tx_bytes: int = 0,
                  rx_dropped: int = 0, tx_dropped: int = 0,
                  rx_errors: int = 0, tx_errors: int = 0):
    d = root / name
    d.mkdir(parents=True)
    (d / "carrier").write_text(carrier + "\n")
    (d / "operstate").write_text(operstate + "\n")
    (d / "speed").write_text(speed + "\n")
    (d / "type").write_text(type_ + "\n")
    stats = d / "statistics"
    stats.mkdir()
    for k, v in dict(
        rx_bytes=rx_bytes, tx_bytes=tx_bytes,
        rx_dropped=rx_dropped, tx_dropped=tx_dropped,
        rx_errors=rx_errors, tx_errors=tx_errors,
    ).items():
        (stats / k).write_text(f"{v}\n")


# --- helpers --------------------------------------------------------

def test_list_interfaces_skips_lo_and_bridges(tmp_path):
    _mk_iface(tmp_path, "lo")
    _mk_iface(tmp_path, "ens18")
    _mk_iface(tmp_path, "docker0")
    # docker0 is a Docker bridge — filtered for LAN-served inference
    assert nic_health.list_interfaces(str(tmp_path)) == ["ens18"]


def test_list_interfaces_sorted(tmp_path):
    _mk_iface(tmp_path, "wlan0")
    _mk_iface(tmp_path, "eth1")
    _mk_iface(tmp_path, "eth0")
    assert nic_health.list_interfaces(str(tmp_path)) == ["eth0", "eth1", "wlan0"]


def test_list_interfaces_empty(tmp_path):
    assert nic_health.list_interfaces(str(tmp_path / "absent")) == []


def test_read_stat_returns_int(tmp_path):
    _mk_iface(tmp_path, "eth0", rx_bytes=12345)
    assert nic_health.read_stat(str(tmp_path), "eth0", "rx_bytes") == 12345


def test_read_stat_missing_returns_none(tmp_path):
    _mk_iface(tmp_path, "eth0")
    assert nic_health.read_stat(str(tmp_path), "eth0", "nonsense") is None


def test_read_attr_returns_string(tmp_path):
    _mk_iface(tmp_path, "eth0", operstate="up")
    assert nic_health.read_attr(str(tmp_path), "eth0", "operstate") == "up"


def test_read_attr_missing_returns_none(tmp_path):
    _mk_iface(tmp_path, "eth0")
    assert nic_health.read_attr(str(tmp_path), "eth0", "missing") is None


# --- is_relevant --------------------------------------------------

def test_is_relevant_keeps_physical():
    assert nic_health.is_relevant("eth0", "1")
    assert nic_health.is_relevant("ens18", "1")
    assert nic_health.is_relevant("enp4s0", "1")
    assert nic_health.is_relevant("wlan0", "1")


def test_is_relevant_filters_lo():
    assert not nic_health.is_relevant("lo", "772")


def test_is_relevant_filters_docker_bridge():
    assert not nic_health.is_relevant("docker0", "1")
    assert not nic_health.is_relevant("virbr0", "1")
    assert not nic_health.is_relevant("br-abc", "1")


def test_is_relevant_filters_veth_pairs():
    assert not nic_health.is_relevant("veth9a8b", "1")


def test_is_relevant_filters_tap():
    assert not nic_health.is_relevant("tap0", "1")


# --- classify ------------------------------------------------------

def test_classify_clean_at_idle():
    v = nic_health.classify({
        "name": "ens18", "carrier": "1", "operstate": "up",
        "speed": 1000,
        "rx_bytes": 1_000_000, "tx_bytes": 1_000_000,
        "rx_dropped": 0, "tx_dropped": 0,
        "rx_errors": 0, "tx_errors": 0,
    })
    assert v["verdict"] == "clean"


def test_classify_link_down():
    v = nic_health.classify({
        "name": "wlan0", "carrier": "0", "operstate": "down",
        "speed": -1,
        "rx_bytes": 0, "tx_bytes": 0,
        "rx_dropped": 0, "tx_dropped": 0,
        "rx_errors": 0, "tx_errors": 0,
    })
    assert v["verdict"] == "link_down"


def test_classify_high_rx_drops():
    v = nic_health.classify({
        "name": "ens18", "carrier": "1", "operstate": "up",
        "speed": 1000,
        "rx_bytes": 3_916_603_149, "tx_bytes": 15_625_495_285,
        "rx_dropped": 6518, "tx_dropped": 0,
        "rx_errors": 0, "tx_errors": 0,
    })
    assert v["verdict"] == "drops_high"
    assert "6518" in v["reason"] or "rx_dropped" in v["reason"]


def test_classify_errors_present_outranks_drops():
    v = nic_health.classify({
        "name": "ens18", "carrier": "1", "operstate": "up",
        "speed": 1000,
        "rx_bytes": 1_000_000, "tx_bytes": 1_000_000,
        "rx_dropped": 10000, "tx_dropped": 0,
        "rx_errors": 50, "tx_errors": 0,
    })
    assert v["verdict"] == "errors_present"


def test_classify_speed_low_at_100m():
    v = nic_health.classify({
        "name": "ens18", "carrier": "1", "operstate": "up",
        "speed": 100,
        "rx_bytes": 0, "tx_bytes": 0,
        "rx_dropped": 0, "tx_dropped": 0,
        "rx_errors": 0, "tx_errors": 0,
    })
    assert v["verdict"] == "speed_low"


def test_classify_speed_unknown_minus_one_is_clean():
    # virtio + bridges report speed=-1 ; not a real signal
    v = nic_health.classify({
        "name": "ens18", "carrier": "1", "operstate": "up",
        "speed": -1,
        "rx_bytes": 1_000_000, "tx_bytes": 1_000_000,
        "rx_dropped": 0, "tx_dropped": 0,
        "rx_errors": 0, "tx_errors": 0,
    })
    assert v["verdict"] == "clean"


def test_classify_unknown_when_carrier_missing():
    v = nic_health.classify({
        "name": "ens18", "carrier": None, "operstate": None,
        "speed": None,
        "rx_bytes": None, "tx_bytes": None,
        "rx_dropped": None, "tx_dropped": None,
        "rx_errors": None, "tx_errors": None,
    })
    assert v["verdict"] == "unknown"


def test_classify_drops_high_has_recipe():
    v = nic_health.classify({
        "name": "ens18", "carrier": "1", "operstate": "up",
        "speed": 1000,
        "rx_bytes": 3_916_603_149, "tx_bytes": 1_000_000,
        "rx_dropped": 6518, "tx_dropped": 0,
        "rx_errors": 0, "tx_errors": 0,
    })
    assert "ethtool" in v["recommendation"].lower() or "ring" in v["recommendation"].lower()


# --- status -------------------------------------------------------

def test_status_no_relevant_ifaces(tmp_path, monkeypatch):
    _mk_iface(tmp_path, "lo")
    _mk_iface(tmp_path, "docker0")
    monkeypatch.setattr(nic_health, "_NET_ROOT", str(tmp_path))
    s = nic_health.status()
    assert s["ok"] is True
    assert s["worst_verdict"] == "no_nics"


def test_status_live_drops_case(tmp_path, monkeypatch):
    # The live-rig state
    _mk_iface(tmp_path, "ens18", carrier="1", operstate="up",
              speed="-1", rx_bytes=3_916_603_149, tx_bytes=15_625_495_285,
              rx_dropped=6518)
    monkeypatch.setattr(nic_health, "_NET_ROOT", str(tmp_path))
    s = nic_health.status()
    assert s["worst_verdict"] == "drops_high"
    iface = s["interfaces"][0]
    assert iface["name"] == "ens18"
    assert iface["rx_dropped"] == 6518


def test_status_picks_worst_across(tmp_path, monkeypatch):
    _mk_iface(tmp_path, "eth0", carrier="1", operstate="up", speed="1000")
    _mk_iface(tmp_path, "eth1", carrier="0", operstate="down", speed="-1")
    monkeypatch.setattr(nic_health, "_NET_ROOT", str(tmp_path))
    s = nic_health.status()
    assert s["worst_verdict"] == "link_down"


def test_status_includes_totals(tmp_path, monkeypatch):
    _mk_iface(tmp_path, "ens18", rx_bytes=1_000_000_000,
              tx_bytes=2_000_000_000)
    monkeypatch.setattr(nic_health, "_NET_ROOT", str(tmp_path))
    s = nic_health.status()
    assert s["total_rx_bytes"] == 1_000_000_000
    assert s["total_tx_bytes"] == 2_000_000_000
