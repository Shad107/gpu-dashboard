"""Tests for modules/ata_port_sata_audit.py — R&D #71.1."""
from __future__ import annotations

import pytest

from gpu_dashboard.modules import ata_port_sata_audit as mod


def _mk_port(root, name, port_no=1):
    d = root / name
    d.mkdir(parents=True, exist_ok=True)
    (d / "port_no").write_text(f"{port_no}\n")
    (d / "idle_irq").write_text("0\n")


def _mk_link(root, name, *,
                 sata_spd="6.0 Gbps",
                 sata_spd_limit="6.0 Gbps",
                 hw_sata_spd_limit="6.0 Gbps"):
    d = root / name
    d.mkdir(parents=True, exist_ok=True)
    (d / "sata_spd").write_text(sata_spd + "\n")
    (d / "sata_spd_limit").write_text(sata_spd_limit + "\n")
    (d / "hw_sata_spd_limit").write_text(
        hw_sata_spd_limit + "\n")


def _mk_device(root, name, *, class_="ata",
                   dma_mode="XFER_UDMA_6",
                   xfer_mode="XFER_UDMA_6", spdn_cnt=0):
    d = root / name
    d.mkdir(parents=True, exist_ok=True)
    (d / "class").write_text(class_ + "\n")
    (d / "dma_mode").write_text(dma_mode + "\n")
    (d / "xfer_mode").write_text(xfer_mode + "\n")
    (d / "spdn_cnt").write_text(f"{spdn_cnt}\n")


# --- parse_gbps -------------------------------------------------

def test_parse_gbps():
    assert mod.parse_gbps("6.0 Gbps") == 6.0
    assert mod.parse_gbps("1.5 Gbps") == 1.5
    assert mod.parse_gbps("3.0 Gbps") == 3.0


def test_parse_gbps_unknown():
    assert mod.parse_gbps("<unknown>") is None
    assert mod.parse_gbps("") is None
    assert mod.parse_gbps(None) is None


# --- list_ata_links --------------------------------------------

def test_list_links_missing(tmp_path):
    assert mod.list_ata_links(str(tmp_path / "nope")) == []


def test_list_links(tmp_path):
    _mk_link(tmp_path, "link1")
    _mk_link(tmp_path, "link2", sata_spd="3.0 Gbps")
    out = mod.list_ata_links(str(tmp_path))
    assert len(out) == 2
    assert out[1]["sata_spd"] == 3.0


# --- list_ata_devices ------------------------------------------

def test_list_devices(tmp_path):
    _mk_device(tmp_path, "dev1.0", spdn_cnt=2)
    out = mod.list_ata_devices(str(tmp_path))
    assert out[0]["spdn_cnt"] == 2


# --- classify ---------------------------------------------------

def test_classify_unknown():
    v = mod.classify([], [], [], False)
    assert v["verdict"] == "unknown"


def test_classify_spdn():
    v = mod.classify(
        [{"id": "ata1", "port_no": 1}],
        [{"id": "link1", "sata_spd": 6.0,
            "sata_spd_limit": 6.0,
            "hw_sata_spd_limit": 6.0}],
        [{"id": "dev1.0", "class": "ata",
            "dma_mode": "XFER_UDMA_6",
            "xfer_mode": "XFER_UDMA_6", "spdn_cnt": 1}],
        True)
    assert v["verdict"] == "link_renegotiated_down"


def test_classify_excessive_errors():
    # sata_spd < hw_limit but no user cap → errors
    v = mod.classify(
        [{"id": "ata1", "port_no": 1}],
        [{"id": "link1", "sata_spd": 3.0,
            "sata_spd_limit": 6.0,
            "hw_sata_spd_limit": 6.0}],
        [{"id": "dev1.0", "class": "ata",
            "dma_mode": "XFER_UDMA_6",
            "xfer_mode": "XFER_UDMA_6", "spdn_cnt": 0}],
        True)
    assert v["verdict"] == "excessive_errors"


def test_classify_spd_limit_capped():
    v = mod.classify(
        [{"id": "ata1", "port_no": 1}],
        [{"id": "link1", "sata_spd": 3.0,
            "sata_spd_limit": 3.0,
            "hw_sata_spd_limit": 6.0}],
        [{"id": "dev1.0", "class": "ata",
            "dma_mode": "XFER_UDMA_6",
            "xfer_mode": "XFER_UDMA_6", "spdn_cnt": 0}],
        True)
    assert v["verdict"] == "spd_limit_capped"


def test_classify_ok():
    v = mod.classify(
        [{"id": "ata1", "port_no": 1}],
        [{"id": "link1", "sata_spd": 6.0,
            "sata_spd_limit": 6.0,
            "hw_sata_spd_limit": 6.0}],
        [{"id": "dev1.0", "class": "ata",
            "dma_mode": "XFER_UDMA_6",
            "xfer_mode": "XFER_UDMA_6", "spdn_cnt": 0}],
        True)
    assert v["verdict"] == "ok"


def test_classify_ok_unknown_speed():
    # KVM virt — sata_spd is <unknown>, no fault
    v = mod.classify(
        [{"id": "ata1", "port_no": 1}],
        [{"id": "link1", "sata_spd": None,
            "sata_spd_limit": None,
            "hw_sata_spd_limit": None}],
        [{"id": "dev1.0", "class": "unknown",
            "dma_mode": None, "xfer_mode": None,
            "spdn_cnt": 0}],
        True)
    assert v["verdict"] == "ok"


# Priority : spdn > errors > capped
def test_priority_spdn_over_errors():
    v = mod.classify(
        [{"id": "ata1", "port_no": 1}],
        [{"id": "link1", "sata_spd": 3.0,
            "sata_spd_limit": 6.0,
            "hw_sata_spd_limit": 6.0}],
        [{"id": "dev1.0", "class": "ata",
            "dma_mode": "XFER_UDMA_6",
            "xfer_mode": "XFER_UDMA_6", "spdn_cnt": 1}],
        True)
    assert v["verdict"] == "link_renegotiated_down"


def test_priority_errors_over_capped():
    # link1 has both downgrade-without-cap (errors) AND link2
    # has a cap. errors > capped in priority.
    v = mod.classify(
        [{"id": "ata1", "port_no": 1}],
        [{"id": "link1", "sata_spd": 3.0,
            "sata_spd_limit": 6.0,
            "hw_sata_spd_limit": 6.0},
          {"id": "link2", "sata_spd": 1.5,
            "sata_spd_limit": 1.5,
            "hw_sata_spd_limit": 6.0}],
        [], True)
    assert v["verdict"] == "excessive_errors"


# --- status integration ----------------------------------------

def test_status_unknown(tmp_path):
    out = mod.status(None,
                          str(tmp_path / "no_port"),
                          str(tmp_path / "no_link"),
                          str(tmp_path / "no_dev"))
    assert out["ok"] is False
    assert out["verdict"]["verdict"] == "unknown"


def test_status_ok_synthetic(tmp_path):
    port_dir = tmp_path / "port"; port_dir.mkdir()
    _mk_port(port_dir, "ata1")
    link_dir = tmp_path / "link"; link_dir.mkdir()
    _mk_link(link_dir, "link1")
    dev_dir = tmp_path / "dev"; dev_dir.mkdir()
    _mk_device(dev_dir, "dev1.0")
    out = mod.status(None, str(port_dir),
                          str(link_dir), str(dev_dir))
    assert out["ok"] is True
    assert out["port_count"] == 1
    assert out["verdict"]["verdict"] == "ok"


def test_status_spdn_synthetic(tmp_path):
    port_dir = tmp_path / "port"; port_dir.mkdir()
    _mk_port(port_dir, "ata1")
    link_dir = tmp_path / "link"; link_dir.mkdir()
    _mk_link(link_dir, "link1")
    dev_dir = tmp_path / "dev"; dev_dir.mkdir()
    _mk_device(dev_dir, "dev1.0", spdn_cnt=3)
    out = mod.status(None, str(port_dir),
                          str(link_dir), str(dev_dir))
    assert out["verdict"]["verdict"] == "link_renegotiated_down"
