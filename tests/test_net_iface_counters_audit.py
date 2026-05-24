"""Tests for modules/net_iface_counters_audit.py — R&D #78.1."""
from __future__ import annotations

import pytest

from gpu_dashboard.modules import net_iface_counters_audit as mod


def _mk_iface(root, name, *, type_=1, operstate="up",
                  carrier_changes=2, stats=None):
    d = root / name
    d.mkdir(parents=True, exist_ok=True)
    (d / "type").write_text(f"{type_}\n")
    (d / "operstate").write_text(operstate + "\n")
    (d / "carrier_changes").write_text(f"{carrier_changes}\n")
    stats_dir = d / "statistics"
    stats_dir.mkdir(exist_ok=True)
    default_stats = {k: 0 for k in mod._COUNTERS}
    default_stats["rx_packets"] = 100000
    if stats:
        default_stats.update(stats)
    for k, v in default_stats.items():
        (stats_dir / k).write_text(f"{v}\n")


# --- list_interfaces -------------------------------------------

def test_list_missing(tmp_path):
    assert mod.list_interfaces(str(tmp_path / "nope")) == []


def test_list(tmp_path):
    _mk_iface(tmp_path, "eth0")
    _mk_iface(tmp_path, "lo", type_=772)
    assert mod.list_interfaces(str(tmp_path)) == ["eth0", "lo"]


# --- read_iface_stats ------------------------------------------

def test_read_stats(tmp_path):
    _mk_iface(tmp_path, "eth0",
                  stats={"rx_crc_errors": 5,
                            "rx_packets": 50000})
    out = mod.read_iface_stats(str(tmp_path), "eth0")
    assert out["rx_crc_errors"] == 5
    assert out["type"] == 1
    assert out["operstate"] == "up"


# --- is_ethernet_or_wifi ---------------------------------------

def test_is_eth_yes():
    assert mod.is_ethernet_or_wifi({"type": 1}) is True


def test_is_eth_no_loopback():
    assert mod.is_ethernet_or_wifi({"type": 772}) is False


# --- classify ---------------------------------------------------

def _good():
    return {"type": 1, "operstate": "up", "carrier_changes": 2,
              "rx_errors": 0, "tx_errors": 0, "rx_dropped": 0,
              "tx_dropped": 0, "collisions": 0,
              "rx_crc_errors": 0, "rx_frame_errors": 0,
              "rx_over_errors": 0, "tx_aborted_errors": 0,
              "rx_packets": 100000, "tx_packets": 50000}


def test_classify_unknown():
    v = mod.classify(False, {})
    assert v["verdict"] == "unknown"


def test_classify_ok():
    v = mod.classify(True, {"eth0": _good()})
    assert v["verdict"] == "ok"


def test_classify_only_loopback():
    v = mod.classify(True,
                          {"lo": {**_good(), "type": 772}})
    assert v["verdict"] == "ok"


def test_classify_rx_crc_storm():
    s = _good()
    s["rx_crc_errors"] = 1
    v = mod.classify(True, {"eth0": s})
    assert v["verdict"] == "rx_crc_storm"


def test_classify_tx_errors():
    s = _good()
    s["tx_errors"] = 100
    v = mod.classify(True, {"eth0": s})
    assert v["verdict"] == "tx_errors_climbing"


def test_classify_carrier_flapping():
    s = _good()
    s["carrier_changes"] = 250
    v = mod.classify(True, {"eth0": s})
    assert v["verdict"] == "carrier_flapping"


def test_classify_rx_dropped_elevated():
    s = _good()
    s["rx_dropped"] = 10000  # 10% of 100k packets
    v = mod.classify(True, {"eth0": s})
    assert v["verdict"] == "rx_dropped_elevated"


def test_classify_drop_skipped_low_traffic():
    # < floor packets — don't ratio-judge
    s = _good()
    s["rx_packets"] = 100
    s["rx_dropped"] = 50  # 50% but only 100 packets total
    v = mod.classify(True, {"eth0": s})
    assert v["verdict"] == "ok"


# Priority : crc > tx_err > flap > drop
def test_priority_crc_over_tx():
    s = _good()
    s["rx_crc_errors"] = 1
    s["tx_errors"] = 100
    v = mod.classify(True, {"eth0": s})
    assert v["verdict"] == "rx_crc_storm"


def test_priority_tx_over_flap():
    s = _good()
    s["tx_errors"] = 100
    s["carrier_changes"] = 250
    v = mod.classify(True, {"eth0": s})
    assert v["verdict"] == "tx_errors_climbing"


# --- status integration ----------------------------------------

def test_status_unknown(tmp_path):
    out = mod.status(None, str(tmp_path / "nope"))
    assert out["ok"] is False
    assert out["verdict"]["verdict"] == "unknown"


def test_status_ok_synthetic(tmp_path):
    _mk_iface(tmp_path, "eth0")
    _mk_iface(tmp_path, "lo", type_=772)
    out = mod.status(None, str(tmp_path))
    assert out["ok"] is True
    assert out["iface_count"] == 2
    assert out["verdict"]["verdict"] == "ok"


def test_status_crc_synthetic(tmp_path):
    _mk_iface(tmp_path, "eth0",
                  stats={"rx_crc_errors": 3})
    out = mod.status(None, str(tmp_path))
    assert out["verdict"]["verdict"] == "rx_crc_storm"
