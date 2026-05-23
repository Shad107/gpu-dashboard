"""Tests for modules/nic_ring_audit.py — R&D #43.4."""
from __future__ import annotations

from pathlib import Path

import pytest

from gpu_dashboard.modules import nic_ring_audit as mod


def _mk_dev(sys_net: Path, dev: str, *, operstate: str = "up",
              carrier: int = 1, mtu: int = 1500, **stats):
    ddir = sys_net / dev
    sdir = ddir / "statistics"
    sdir.mkdir(parents=True, exist_ok=True)
    (ddir / "operstate").write_text(operstate + "\n")
    (ddir / "carrier").write_text(str(carrier) + "\n")
    (ddir / "mtu").write_text(str(mtu) + "\n")
    for k, v in stats.items():
        (sdir / k).write_text(str(v) + "\n")


# --- list_devices --------------------------------------------------

def test_list_devices_skips_lo(tmp_path):
    _mk_dev(tmp_path, "lo")
    _mk_dev(tmp_path, "eth0")
    assert mod.list_devices(str(tmp_path)) == ["eth0"]


def test_list_devices_missing(tmp_path):
    assert mod.list_devices(str(tmp_path / "nope")) == []


# --- read_device ---------------------------------------------------

def test_read_device_basic(tmp_path):
    _mk_dev(tmp_path, "eth0", rx_dropped=5, rx_packets=1000,
              tx_packets=500, rx_bytes=10000)
    d = mod.read_device(str(tmp_path), "eth0")
    assert d["dev"] == "eth0"
    assert d["rx_dropped"] == 5
    assert d["rx_packets"] == 1000


def test_read_device_missing_stats(tmp_path):
    # Stats dir exists but counters absent — should still return.
    sdir = tmp_path / "eth0" / "statistics"
    sdir.mkdir(parents=True)
    (tmp_path / "eth0" / "operstate").write_text("up\n")
    (tmp_path / "eth0" / "carrier").write_text("1\n")
    (tmp_path / "eth0" / "mtu").write_text("1500\n")
    d = mod.read_device(str(tmp_path), "eth0")
    assert d["dev"] == "eth0"
    assert "rx_dropped" not in d


# --- is_up ---------------------------------------------------------

def test_is_up_operstate_up():
    assert mod.is_up({"operstate": "up", "carrier": 1}) is True


def test_is_up_operstate_down():
    assert mod.is_up({"operstate": "down", "carrier": 0}) is False


def test_is_up_carrier_unknown_op():
    assert mod.is_up({"operstate": "unknown", "carrier": 1}) is True


# --- classify ------------------------------------------------------

def _dev(name="eth0", up=True, **stats):
    base = {"dev": name,
              "operstate": "up" if up else "down",
              "carrier": 1 if up else 0}
    base.update(stats)
    return base


def test_classify_unknown_when_empty():
    v = mod.classify([])
    assert v["verdict"] == "unknown"


def test_classify_no_active_nic():
    v = mod.classify([_dev(up=False)])
    assert v["verdict"] == "no_active_nic"


def test_classify_ok():
    v = mod.classify([_dev(rx_packets=10000, rx_dropped=0,
                              tx_packets=10000, tx_dropped=0)])
    assert v["verdict"] == "ok"


def test_classify_fifo_overrun():
    v = mod.classify([_dev(rx_packets=100000, rx_fifo_errors=12)])
    assert v["verdict"] == "fifo_overrun"
    assert "ethtool" in v["recommendation"]


def test_classify_fifo_via_rx_missed():
    # Some drivers expose the same overrun as rx_missed_errors.
    v = mod.classify([_dev(rx_packets=100000, rx_missed_errors=8)])
    assert v["verdict"] == "fifo_overrun"


def test_classify_rx_drops_climbing():
    # 0.5 % drop rate on > 10k packets.
    v = mod.classify([_dev(rx_packets=100000, rx_dropped=500)])
    assert v["verdict"] == "rx_drops_climbing"
    assert "netdev_max_backlog" in v["recommendation"]


def test_classify_rx_drops_ignored_below_threshold():
    # 0.05 % drop rate — below 0.1 % threshold → not flagged.
    v = mod.classify([_dev(rx_packets=100000, rx_dropped=50)])
    assert v["verdict"] == "ok"


def test_classify_rx_drops_ignored_when_few_packets():
    # 100 % drop but < 10k packets → don't classify off tiny sample.
    v = mod.classify([_dev(rx_packets=10, rx_dropped=10)])
    assert v["verdict"] == "ok"


def test_classify_cable_or_duplex_via_crc():
    v = mod.classify([_dev(rx_packets=10000, rx_crc_errors=5)])
    assert v["verdict"] == "cable_or_duplex"
    assert "duplex" in v["recommendation"]


def test_classify_cable_or_duplex_via_frame():
    v = mod.classify([_dev(rx_packets=10000, rx_frame_errors=5)])
    assert v["verdict"] == "cable_or_duplex"


def test_classify_tx_drops():
    v = mod.classify([_dev(rx_packets=10000,
                              tx_packets=100000, tx_dropped=2000)])
    assert v["verdict"] == "tx_drops"
    assert "txqueuelen" in v["recommendation"]


def test_classify_priority_fifo_beats_rx_drops():
    # If both are bad, fifo (hardware) > rx_drops (kernel backlog).
    v = mod.classify([_dev(rx_packets=100000, rx_fifo_errors=3,
                              rx_dropped=500)])
    assert v["verdict"] == "fifo_overrun"


def test_classify_priority_rx_drops_beats_cable():
    v = mod.classify([_dev(rx_packets=100000, rx_dropped=500,
                              rx_crc_errors=1)])
    assert v["verdict"] == "rx_drops_climbing"


def test_classify_worst_across_devices():
    # eth0 OK, eth1 has CRC errors → cable wins (higher rank than ok).
    devs = [_dev(name="eth0", rx_packets=10000, rx_dropped=0),
              _dev(name="eth1", rx_packets=10000, rx_crc_errors=2)]
    v = mod.classify(devs)
    assert v["verdict"] == "cable_or_duplex"
    assert "eth1" in v["reason"]


# --- status integration --------------------------------------------

def test_status_with_isolated_root(monkeypatch, tmp_path):
    sys_net = tmp_path / "net"
    sys_net.mkdir()
    _mk_dev(sys_net, "lo")
    _mk_dev(sys_net, "eth0", rx_packets=100000, rx_fifo_errors=2)
    monkeypatch.setattr(mod, "_SYS_NET", str(sys_net))
    out = mod.status()
    assert out["ok"] is True
    assert out["device_count"] == 1  # lo filtered
    assert out["verdict"]["verdict"] == "fifo_overrun"


def test_status_unknown_when_missing(monkeypatch, tmp_path):
    monkeypatch.setattr(mod, "_SYS_NET", str(tmp_path / "nope"))
    out = mod.status()
    assert out["ok"] is False
    assert out["verdict"]["verdict"] == "unknown"
