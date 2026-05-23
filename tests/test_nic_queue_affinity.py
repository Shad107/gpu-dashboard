"""Tests for modules/nic_queue_affinity.py — R&D #40.4."""
from __future__ import annotations

from pathlib import Path

import pytest

from gpu_dashboard.modules import nic_queue_affinity as mod


# --- parse_cpu_mask ------------------------------------------------

def test_parse_cpu_mask_simple():
    assert mod.parse_cpu_mask("ff") == set(range(8))


def test_parse_cpu_mask_zero():
    assert mod.parse_cpu_mask("000") == set()


def test_parse_cpu_mask_high_cpu():
    # 0x01,00000000 → bit 32 set
    assert mod.parse_cpu_mask("00000001,00000000") == {32}


def test_parse_cpu_mask_with_commas():
    # 0x000000ff → 0..7
    assert mod.parse_cpu_mask("00000000,000000ff") == set(range(8))


def test_parse_cpu_mask_empty():
    assert mod.parse_cpu_mask("") == set()
    assert mod.parse_cpu_mask(",,") == set()


def test_parse_cpu_mask_bad_hex():
    assert mod.parse_cpu_mask("nope") == set()


# --- read_device ---------------------------------------------------

def _mk_dev(sys_net: Path, dev: str, *, rx_queues: int = 1,
              tx_queues: int = 1, rps_cpus: str = "00",
              xps_cpus: str = "00", rps_flow_cnt: int = 0,
              operstate: str = "up", carrier: int = 1,
              tx_queue_len: int = 1000, mtu: int = 1500,
              gro_flush_timeout: int = 0,
              napi_defer_hard_irqs: int = 0):
    ddir = sys_net / dev
    ddir.mkdir(parents=True, exist_ok=True)
    (ddir / "operstate").write_text(operstate + "\n")
    (ddir / "carrier").write_text(str(carrier) + "\n")
    (ddir / "type").write_text("1\n")
    (ddir / "tx_queue_len").write_text(str(tx_queue_len) + "\n")
    (ddir / "mtu").write_text(str(mtu) + "\n")
    (ddir / "gro_flush_timeout").write_text(
        str(gro_flush_timeout) + "\n")
    (ddir / "napi_defer_hard_irqs").write_text(
        str(napi_defer_hard_irqs) + "\n")
    q = ddir / "queues"
    q.mkdir(exist_ok=True)
    for i in range(rx_queues):
        rxd = q / f"rx-{i}"
        rxd.mkdir(parents=True, exist_ok=True)
        (rxd / "rps_cpus").write_text(rps_cpus + "\n")
        (rxd / "rps_flow_cnt").write_text(str(rps_flow_cnt) + "\n")
    for i in range(tx_queues):
        txd = q / f"tx-{i}"
        txd.mkdir(parents=True, exist_ok=True)
        (txd / "xps_cpus").write_text(xps_cpus + "\n")
        (txd / "byte_queue_limits").mkdir(exist_ok=True)
        (txd / "byte_queue_limits" / "limit").write_text("0\n")


def test_read_device_basic(tmp_path):
    _mk_dev(tmp_path, "eth0", rx_queues=4, tx_queues=4)
    d = mod.read_device(str(tmp_path), "eth0")
    assert d["dev"] == "eth0"
    assert d["operstate"] == "up"
    assert d["rx_queue_count"] == 4
    assert d["tx_queue_count"] == 4
    assert d["rx_queues"][0]["name"] == "rx-0"
    assert d["mtu"] == 1500


def test_read_device_rps_cpus_parsed(tmp_path):
    _mk_dev(tmp_path, "eth0", rx_queues=1, rps_cpus="ff")
    d = mod.read_device(str(tmp_path), "eth0")
    assert d["rx_queues"][0]["rps_cpus"] == sorted(range(8))


def test_list_devices_skips_lo(tmp_path):
    _mk_dev(tmp_path, "lo")
    _mk_dev(tmp_path, "eth0")
    assert mod.list_devices(str(tmp_path)) == ["eth0"]


def test_list_devices_missing(tmp_path):
    assert mod.list_devices(str(tmp_path / "nope")) == []


# --- is_up ---------------------------------------------------------

def test_is_up_operstate_up():
    assert mod.is_up({"operstate": "up", "carrier": 1}) is True


def test_is_up_operstate_down():
    assert mod.is_up({"operstate": "down", "carrier": 0}) is False


def test_is_up_carrier_with_unknown_op():
    assert mod.is_up({"operstate": "unknown", "carrier": 1}) is True


# --- classify ------------------------------------------------------

def _dev(name="eth0", rx=4, tx=4, rps_cpus=None, xps_cpus=None,
          rps_flow_cnt=0, operstate="up", carrier=1):
    if rps_cpus is None:
        rps_cpus = [list(range(8))] * rx
    if xps_cpus is None:
        xps_cpus = [list(range(8))] * tx
    rxq = [{"name": f"rx-{i}", "rps_cpus": rps_cpus[i],
              "rps_flow_cnt": rps_flow_cnt}
            for i in range(rx)]
    txq = [{"name": f"tx-{i}", "xps_cpus": xps_cpus[i]}
            for i in range(tx)]
    return {"dev": name, "operstate": operstate, "carrier": carrier,
              "rx_queue_count": rx, "tx_queue_count": tx,
              "rx_queues": rxq, "tx_queues": txq}


def test_classify_unknown_when_empty():
    v = mod.classify([])
    assert v["verdict"] == "unknown"


def test_classify_no_active_nic():
    v = mod.classify([_dev(operstate="down", carrier=0)])
    assert v["verdict"] == "no_active_nic"


def test_classify_single_queue_nic():
    v = mod.classify([_dev(rx=1, tx=1)])
    assert v["verdict"] == "single_queue_nic"


def test_classify_multi_queue_no_rps():
    devs = [_dev(rx=4, tx=4, rps_cpus=[[]] * 4, xps_cpus=[[0], [1], [2], [3]],
                  rps_flow_cnt=4096)]
    v = mod.classify(devs)
    assert v["verdict"] == "multi_queue_no_rps"
    assert "rps_cpus" in v["recommendation"]


def test_classify_xps_single_cpu_bottleneck():
    devs = [_dev(rx=4, tx=4,
                  rps_cpus=[[0, 1, 2, 3]] * 4,
                  xps_cpus=[[0]] * 4,
                  rps_flow_cnt=4096)]
    v = mod.classify(devs)
    assert v["verdict"] == "xps_single_cpu_bottleneck"
    assert "xps_cpus" in v["recommendation"]


def test_classify_rfs_disabled():
    devs = [_dev(rx=4, tx=4,
                  rps_cpus=[[0, 1, 2, 3]] * 4,
                  xps_cpus=[[0, 1], [2, 3], [4, 5], [6, 7]],
                  rps_flow_cnt=0)]
    v = mod.classify(devs)
    assert v["verdict"] == "rfs_disabled"
    assert "rps_flow_cnt" in v["recommendation"]


def test_classify_ok():
    devs = [_dev(rx=4, tx=4,
                  rps_cpus=[[0, 1, 2, 3]] * 4,
                  xps_cpus=[[0, 1], [2, 3], [4, 5], [6, 7]],
                  rps_flow_cnt=4096)]
    v = mod.classify(devs)
    assert v["verdict"] == "ok"


def test_classify_rps_misaligned_with_gpu_numa():
    # GPU is on NUMA cpus {0,1,2,3} ; rx-0 rps_cpus={8,9} → outside
    devs = [_dev(rx=2, tx=2,
                  rps_cpus=[[8, 9], [0, 1]],
                  xps_cpus=[[0, 1], [2, 3]],
                  rps_flow_cnt=4096)]
    v = mod.classify(devs, gpu_numa_cpus={0, 1, 2, 3})
    assert v["verdict"] == "rps_misaligned_with_gpu_numa"


def test_classify_worst_wins_across_devices():
    a = _dev(name="eth0", rx=1, tx=1)
    b = _dev(name="eth1", rx=4, tx=4,
              rps_cpus=[[]] * 4,
              xps_cpus=[[0, 1]] * 4,
              rps_flow_cnt=4096)
    v = mod.classify([a, b])
    assert v["verdict"] == "multi_queue_no_rps"
    assert "eth1" in v["reason"]


# --- status integration -------------------------------------------

def test_status_no_sys_class_net(monkeypatch, tmp_path):
    monkeypatch.setattr(mod, "_SYS_NET", str(tmp_path / "nope"))
    out = mod.status()
    assert out["ok"] is False
    assert out["verdict"]["verdict"] == "unknown"


def test_status_with_isolated_root(monkeypatch, tmp_path):
    sys_net = tmp_path / "net"
    sys_net.mkdir()
    _mk_dev(sys_net, "lo")
    _mk_dev(sys_net, "eth0", rx_queues=4, tx_queues=4,
              rps_cpus="0f", xps_cpus="01", rps_flow_cnt=4096)
    monkeypatch.setattr(mod, "_SYS_NET", str(sys_net))
    # Force no GPU cross-ref
    monkeypatch.setattr(mod, "_try_gpu_numa_cpus", lambda cfg: set())
    out = mod.status()
    assert out["ok"] is True
    assert out["device_count"] == 1  # lo filtered
    # xps_cpus=01 → popcount 1 on every TX → bottleneck
    assert out["verdict"]["verdict"] == "xps_single_cpu_bottleneck"
