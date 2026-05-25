"""Tests for modules/bpf_jit_xdp_busy_poll_audit.py
R&D #93.4."""
from __future__ import annotations

import pytest

from gpu_dashboard.modules import (
    bpf_jit_xdp_busy_poll_audit as mod)


def _mk_core(tmp_path, *, bpf_jit_enable="1",
              busy_poll="0", busy_read="0"):
    d = tmp_path / "net_core"
    d.mkdir(parents=True, exist_ok=True)
    if bpf_jit_enable is not None:
        (d / "bpf_jit_enable").write_text(
            bpf_jit_enable + "\n")
    (d / "busy_poll").write_text(busy_poll + "\n")
    (d / "busy_read").write_text(busy_read + "\n")
    return str(d)


def _mk_iface(tmp_path, iface, *, xdp=None):
    """xdp = None → no xdp file ; 'file:<val>' → single-file;
    'dir:<keys>' where keys is dict; missing → no xdp."""
    d = tmp_path / "class_net" / iface
    d.mkdir(parents=True, exist_ok=True)
    if xdp is None:
        return
    if isinstance(xdp, int):
        (d / "xdp").write_text(str(xdp) + "\n")
    elif isinstance(xdp, dict):
        # newer dir-style xdp
        sub = d / "xdp"
        sub.mkdir()
        for k, v in xdp.items():
            (sub / k).write_text(str(v) + "\n")


# --- _iface_has_xdp --------------------------------------------

def test_iface_has_xdp_missing(tmp_path):
    d = tmp_path / "class_net" / "eth0"
    d.mkdir(parents=True)
    assert mod._iface_has_xdp(str(d)) is False


def test_iface_has_xdp_file_zero(tmp_path):
    _mk_iface(tmp_path, "eth0", xdp=0)
    assert mod._iface_has_xdp(
        str(tmp_path / "class_net" / "eth0")) is False


def test_iface_has_xdp_file_nonzero(tmp_path):
    _mk_iface(tmp_path, "eth0", xdp=1)
    assert mod._iface_has_xdp(
        str(tmp_path / "class_net" / "eth0")) is True


def test_iface_has_xdp_dir_all_zero(tmp_path):
    _mk_iface(tmp_path, "eth0",
                  xdp={"generic": 0, "native": 0})
    assert mod._iface_has_xdp(
        str(tmp_path / "class_net" / "eth0")) is False


def test_iface_has_xdp_dir_one_nonzero(tmp_path):
    _mk_iface(tmp_path, "eth0",
                  xdp={"generic": 0, "native": 1})
    assert mod._iface_has_xdp(
        str(tmp_path / "class_net" / "eth0")) is True


# --- find_xdp_ifaces -------------------------------------------

def test_find_xdp_ifaces_missing(tmp_path):
    assert mod.find_xdp_ifaces(
        str(tmp_path / "nope")) == []


def test_find_xdp_ifaces_skips_lo(tmp_path):
    _mk_iface(tmp_path, "lo", xdp=1)
    _mk_iface(tmp_path, "ens18", xdp=1)
    out = mod.find_xdp_ifaces(
        str(tmp_path / "class_net"))
    assert "lo" not in out
    assert "ens18" in out


def test_find_xdp_ifaces_none_attached(tmp_path):
    _mk_iface(tmp_path, "ens18", xdp=0)
    _mk_iface(tmp_path, "docker0", xdp=None)
    out = mod.find_xdp_ifaces(
        str(tmp_path / "class_net"))
    assert out == []


# --- classify --------------------------------------------------

def test_classify_unknown_no_core():
    v = mod.classify({}, [], False)
    assert v["verdict"] == "unknown"


def test_classify_requires_root():
    v = mod.classify({"bpf_jit_enable": None,
                          "busy_poll": 0},
                          [], True)
    assert v["verdict"] == "requires_root"


def test_classify_jit_disabled():
    v = mod.classify({"bpf_jit_enable": 0,
                          "busy_poll": 0},
                          [], True)
    assert v["verdict"] == "jit_disabled"


def test_classify_xdp_attached():
    v = mod.classify({"bpf_jit_enable": 1,
                          "busy_poll": 0},
                          ["ens18"], True)
    assert v["verdict"] == "xdp_attached"
    assert "ens18" in v["ifaces"]


def test_classify_busy_poll_active():
    v = mod.classify({"bpf_jit_enable": 1,
                          "busy_poll": 50},
                          [], True)
    assert v["verdict"] == "busy_poll_active"


def test_classify_ok():
    v = mod.classify({"bpf_jit_enable": 1,
                          "busy_poll": 0},
                          [], True)
    assert v["verdict"] == "ok"


# Priority : jit_disabled > xdp_attached > busy_poll
def test_priority_jit_over_xdp():
    v = mod.classify({"bpf_jit_enable": 0,
                          "busy_poll": 0},
                          ["ens18"], True)
    assert v["verdict"] == "jit_disabled"


def test_priority_xdp_over_busy_poll():
    v = mod.classify({"bpf_jit_enable": 1,
                          "busy_poll": 50},
                          ["ens18"], True)
    assert v["verdict"] == "xdp_attached"


# --- status integration ----------------------------------------

def test_status_unknown(tmp_path):
    out = mod.status(None, str(tmp_path / "nope"),
                       str(tmp_path / "nope_class"))
    assert out["verdict"]["verdict"] == "unknown"


def test_status_ok_synthetic(tmp_path):
    c = _mk_core(tmp_path)
    _mk_iface(tmp_path, "ens18", xdp=0)
    out = mod.status(None, c,
                       str(tmp_path / "class_net"))
    assert out["verdict"]["verdict"] == "ok"


def test_status_jit_disabled_synthetic(tmp_path):
    c = _mk_core(tmp_path, bpf_jit_enable="0")
    out = mod.status(None, c,
                       str(tmp_path / "class_net"))
    assert out["verdict"]["verdict"] == "jit_disabled"
    assert out["ok"] is False


def test_status_xdp_attached_synthetic(tmp_path):
    c = _mk_core(tmp_path)
    _mk_iface(tmp_path, "ens18", xdp=1)
    out = mod.status(None, c,
                       str(tmp_path / "class_net"))
    assert out["verdict"]["verdict"] == "xdp_attached"
    assert "ens18" in out["xdp_attached_ifaces"]
