"""Tests for modules/cxl_dax_memory_audit.py — R&D #70.2."""
from __future__ import annotations

import pytest

from gpu_dashboard.modules import cxl_dax_memory_audit as mod


def _mk_cxl_decoder(root, name, *, mode="ram", size="0x10000000"):
    d = root / name
    d.mkdir(parents=True, exist_ok=True)
    (d / "mode").write_text(mode + "\n")
    (d / "size").write_text(size + "\n")


def _mk_cxl_mem(root, name, *, ram_size=0, pmem_size=0):
    d = root / name
    d.mkdir(parents=True, exist_ok=True)
    (d / "ram").mkdir()
    (d / "pmem").mkdir()
    (d / "ram" / "size").write_text(f"{ram_size}\n")
    (d / "pmem" / "size").write_text(f"{pmem_size}\n")


def _mk_dax(root, name, *, size=1024*1024,
                 target_node=0, align=4096):
    d = root / name
    d.mkdir(parents=True, exist_ok=True)
    (d / "size").write_text(f"{size}\n")
    (d / "target_node").write_text(f"{target_node}\n")
    (d / "align").write_text(f"{align}\n")


def _mk_nd_region(root, name, *, size=1024*1024):
    d = root / name
    d.mkdir(parents=True, exist_ok=True)
    (d / "size").write_text(f"{size}\n")
    (d / "set_cookie").write_text("0xabcdef\n")


# --- list_cxl_devices ------------------------------------------

def test_list_cxl_missing(tmp_path):
    out = mod.list_cxl_devices(str(tmp_path / "nope"))
    assert out == {"decoders": [], "mems": [], "ports": []}


def test_list_cxl_mixed(tmp_path):
    _mk_cxl_decoder(tmp_path, "decoder0.0", mode="ram")
    _mk_cxl_decoder(tmp_path, "decoder1.0", mode="error")
    _mk_cxl_mem(tmp_path, "mem0", ram_size=1024,
                    pmem_size=2048)
    (tmp_path / "port1").mkdir()
    (tmp_path / "port1" / "uport").write_text("up0\n")
    out = mod.list_cxl_devices(str(tmp_path))
    assert len(out["decoders"]) == 2
    assert len(out["mems"]) == 1
    assert out["mems"][0]["ram_size"] == 1024
    assert len(out["ports"]) == 1


# --- list_dax_devices -----------------------------------------

def test_list_dax_missing(tmp_path):
    assert mod.list_dax_devices(str(tmp_path / "nope")) == []


def test_list_dax(tmp_path):
    _mk_dax(tmp_path, "dax0.0", size=1<<30,
                target_node=1)
    out = mod.list_dax_devices(str(tmp_path))
    assert out[0]["size"] == 1 << 30
    assert out[0]["target_node"] == 1


# --- list_nd_regions -------------------------------------------

def test_list_nd_missing(tmp_path):
    assert mod.list_nd_regions(str(tmp_path / "nope")) == []


def test_list_nd_region(tmp_path):
    _mk_nd_region(tmp_path, "region0")
    out = mod.list_nd_regions(str(tmp_path))
    assert out[0]["id"] == "region0"


# --- classify ---------------------------------------------------

def test_classify_unknown():
    v = mod.classify({"decoders": [], "mems": [], "ports": []},
                          [], [], False, False, False)
    assert v["verdict"] == "unknown"


def test_classify_cxl_decoder_error():
    v = mod.classify(
        {"decoders":
            [{"id": "decoder0.0",
                "state": "error",
                "size": "0x0"}],
          "mems": [], "ports": []},
        [], [], True, False, False)
    assert v["verdict"] == "cxl_decoder_error"


def test_classify_dax_zero():
    v = mod.classify(
        {"decoders": [], "mems": [], "ports": []},
        [{"id": "dax0.0", "size": 0, "target_node": 0,
            "align": 4096}],
        [], False, True, False)
    assert v["verdict"] == "dax_size_zero_misconfigured"


def test_classify_target_node_unbound():
    v = mod.classify(
        {"decoders": [], "mems": [], "ports": []},
        [{"id": "dax0.0", "size": 1<<30,
            "target_node": -1, "align": 4096}],
        [], False, True, False)
    assert v["verdict"] == "target_node_unbound"


def test_classify_pmem_unused():
    v = mod.classify(
        {"decoders": [], "mems": [], "ports": []},
        [],
        [{"id": "region0", "size": 1<<30,
            "set_cookie": "0xabc"}],
        False, False, True)
    assert v["verdict"] == "pmem_present_unused"


def test_classify_pmem_with_dax_ok():
    # nd region exists, dax device exists → ok
    v = mod.classify(
        {"decoders": [], "mems": [], "ports": []},
        [{"id": "dax0.0", "size": 1<<30,
            "target_node": 0, "align": 4096}],
        [{"id": "region0", "size": 1<<30,
            "set_cookie": "0xabc"}],
        False, True, True)
    assert v["verdict"] == "ok"


def test_classify_ok_empty_surfaces():
    v = mod.classify({"decoders": [], "mems": [], "ports": []},
                          [], [], True, True, True)
    assert v["verdict"] == "ok"


# Priority : decoder_error > dax_zero > target_node > pmem_unused
def test_priority_decoder_error_over_dax():
    v = mod.classify(
        {"decoders": [{"id": "decoder0.0",
                            "state": "error",
                            "size": "0"}],
          "mems": [], "ports": []},
        [{"id": "dax0.0", "size": 0, "target_node": 0,
            "align": 4096}],
        [], True, True, False)
    assert v["verdict"] == "cxl_decoder_error"


def test_priority_dax_zero_over_target_node():
    v = mod.classify(
        {"decoders": [], "mems": [], "ports": []},
        [{"id": "dax0.0", "size": 0,
            "target_node": -1, "align": 4096}],
        [], False, True, False)
    assert v["verdict"] == "dax_size_zero_misconfigured"


# --- status integration -----------------------------------------

def test_status_unknown(tmp_path):
    out = mod.status(None,
                          str(tmp_path / "no_cxl"),
                          str(tmp_path / "no_dax"),
                          str(tmp_path / "no_nd"))
    assert out["ok"] is False
    assert out["verdict"]["verdict"] == "unknown"


def test_status_pmem_unused(tmp_path):
    nd = tmp_path / "nd"; nd.mkdir()
    _mk_nd_region(nd, "region0")
    out = mod.status(None,
                          str(tmp_path / "no_cxl"),
                          str(tmp_path / "no_dax"),
                          str(nd))
    assert out["verdict"]["verdict"] == "pmem_present_unused"


def test_status_ok_synthetic(tmp_path):
    dax = tmp_path / "dax"; dax.mkdir()
    _mk_dax(dax, "dax0.0", size=1<<30, target_node=0)
    out = mod.status(None,
                          str(tmp_path / "no_cxl"),
                          str(dax),
                          str(tmp_path / "no_nd"))
    assert out["ok"] is True
    assert out["dax_device_count"] == 1
    assert out["verdict"]["verdict"] == "ok"
