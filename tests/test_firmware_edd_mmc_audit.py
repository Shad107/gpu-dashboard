"""Tests for modules/firmware_edd_mmc_audit.py — R&D #64.4."""
from __future__ import annotations

import pytest

from gpu_dashboard.modules import firmware_edd_mmc_audit as mod


def _mk_edd(root, name, mbr_signature="0xdeadbeef"):
    d = root / name
    d.mkdir(parents=True, exist_ok=True)
    (d / "mbr_signature").write_text(mbr_signature + "\n")
    (d / "host_bus").write_text("PCIX(0,0,0)\n")
    (d / "interface").write_text("ATA\n")


def _mk_mmc(root, name, *, type_="MMC", life_time="0x01 0x01",
              dev_name="MMC04G"):
    d = root / name
    d.mkdir(parents=True, exist_ok=True)
    (d / "type").write_text(type_ + "\n")
    (d / "name").write_text(dev_name + "\n")
    (d / "manfid").write_text("0x000045\n")
    (d / "oemid").write_text("0x0100\n")
    (d / "serial").write_text("0x12345678\n")
    (d / "life_time").write_text(life_time + "\n")


def _mk_host(root, name, clock=400000000):
    d = root / name
    d.mkdir(parents=True, exist_ok=True)
    (d / "clock").write_text(f"{clock}\n")


# --- _max_life_time_byte ----------------------------------------

def test_max_life_time_byte():
    assert mod._max_life_time_byte("0x01 0x01") == 1
    assert mod._max_life_time_byte("0x05 0x0a") == 10
    assert mod._max_life_time_byte("0x0B 0x09") == 11
    assert mod._max_life_time_byte(None) is None
    assert mod._max_life_time_byte("") is None


# --- list_* ----------------------------------------------------

def test_list_edd_missing(tmp_path):
    assert mod.list_edd_entries(str(tmp_path / "nope")) == []


def test_list_edd_entries(tmp_path):
    _mk_edd(tmp_path, "int13_dev80")
    out = mod.list_edd_entries(str(tmp_path))
    assert len(out) == 1
    assert out[0]["mbr_signature"] == "0xdeadbeef"


def test_list_mmc_devices_missing(tmp_path):
    assert mod.list_mmc_devices(str(tmp_path / "nope")) == []


def test_list_mmc_devices(tmp_path):
    _mk_mmc(tmp_path, "mmc0:0001")
    out = mod.list_mmc_devices(str(tmp_path))
    assert len(out) == 1


def test_list_mmc_hosts_missing(tmp_path):
    assert mod.list_mmc_hosts(str(tmp_path / "nope")) == []


def test_list_mmc_hosts(tmp_path):
    _mk_host(tmp_path, "mmc0", clock=400_000_000)
    out = mod.list_mmc_hosts(str(tmp_path))
    assert out[0]["clock"] == 400_000_000


# --- classify ---------------------------------------------------

def _edd():
    return {"id": "int13_dev80", "mbr_signature": "0xdead",
              "host_bus": "PCI", "interface": "ATA"}


def _mmc(life="0x01 0x01"):
    return {"id": "mmc0:0001", "type": "MMC", "name": "MMC04G",
              "manfid": "0x45", "oemid": "0x100",
              "serial": "0x12345", "life_time": life}


def _host(clock=400_000_000):
    return {"id": "mmc0", "clock": clock}


def test_classify_unknown():
    v = mod.classify([], [], [])
    assert v["verdict"] == "unknown"


def test_classify_ok():
    v = mod.classify([], [_mmc()], [_host()])
    assert v["verdict"] == "ok"


def test_classify_emmc_wear():
    v = mod.classify([], [_mmc(life="0x0B 0x09")], [_host()])
    assert v["verdict"] == "emmc_wear_imminent"


def test_classify_mmc_clock_legacy():
    v = mod.classify([], [_mmc()],
                       [_host(clock=26_000_000)])
    assert v["verdict"] == "mmc_clock_legacy"


def test_classify_priority_wear_wins():
    v = mod.classify([], [_mmc(life="0x0B 0x09")],
                       [_host(clock=26_000_000)])
    assert v["verdict"] == "emmc_wear_imminent"


# --- status integration -----------------------------------------

def test_status_unknown(tmp_path):
    out = mod.status(None, str(tmp_path / "noedd"),
                       str(tmp_path / "nommc"),
                       str(tmp_path / "nohost"))
    assert out["ok"] is False
    assert out["verdict"]["verdict"] == "unknown"


def test_status_live_like_emmc_wear(tmp_path):
    mmc = tmp_path / "mmc"
    _mk_mmc(mmc, "mmc0:0001", life_time="0x0B 0x09")
    host = tmp_path / "host"
    _mk_host(host, "mmc0", clock=400_000_000)
    out = mod.status(None, str(tmp_path / "noedd"),
                       str(mmc), str(host))
    assert out["ok"] is True
    assert out["mmc_count"] == 1
    assert out["verdict"]["verdict"] == "emmc_wear_imminent"
