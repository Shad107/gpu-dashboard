"""Tests for modules/net_stacking_topology_audit.py — R&D #78.2."""
from __future__ import annotations

import os

import pytest

from gpu_dashboard.modules import net_stacking_topology_audit as mod


def _mk_iface(root, name, *, operstate="up", master=None,
              is_bond=False, is_bridge=False, lowers=None,
              uppers=None, bond_slaves=None,
              bridge_stp_state=None, bridge_ports=None):
    d = root / name
    d.mkdir(parents=True, exist_ok=True)
    (d / "operstate").write_text(operstate + "\n")
    if master is not None:
        master_target = root / master
        master_target.mkdir(parents=True, exist_ok=True)
        os.symlink(str(master_target), str(d / "master"))
    if is_bond:
        b = d / "bonding"
        b.mkdir(exist_ok=True)
        slaves = bond_slaves or []
        (b / "slaves").write_text(" ".join(slaves) + "\n")
        (b / "mode").write_text("active-backup\n")
        (b / "mii_status").write_text("up\n")
        (b / "active_slave").write_text(
            (slaves[0] if slaves else "") + "\n")
    if is_bridge:
        b = d / "bridge"
        b.mkdir(exist_ok=True)
        if bridge_stp_state is not None:
            (b / "stp_state").write_text(f"{bridge_stp_state}\n")
        brif = d / "brif"
        brif.mkdir(exist_ok=True)
        for p in (bridge_ports or []):
            (brif / p).symlink_to(str(root / p))
    for lo in (lowers or []):
        target = root / lo
        target.mkdir(parents=True, exist_ok=True)
        os.symlink(str(target), str(d / f"lower_{lo}"))
    for up in (uppers or []):
        target = root / up
        target.mkdir(parents=True, exist_ok=True)
        os.symlink(str(target), str(d / f"upper_{up}"))


# --- list_interfaces -------------------------------------------

def test_list_missing(tmp_path):
    assert mod.list_interfaces(str(tmp_path / "nope")) == []


def test_list(tmp_path):
    _mk_iface(tmp_path, "eth0")
    _mk_iface(tmp_path, "lo")
    assert mod.list_interfaces(str(tmp_path)) == ["eth0", "lo"]


# --- inspect_iface ---------------------------------------------

def test_inspect_plain(tmp_path):
    _mk_iface(tmp_path, "eth0")
    info = mod.inspect_iface(str(tmp_path), "eth0")
    assert info["operstate"] == "up"
    assert info["is_bond"] is False
    assert info["is_bridge"] is False
    assert info["master"] is None


def test_inspect_bond(tmp_path):
    _mk_iface(tmp_path, "eth0", master="bond0")
    _mk_iface(tmp_path, "eth1", master="bond0")
    _mk_iface(tmp_path, "bond0", is_bond=True,
              bond_slaves=["eth0", "eth1"])
    info = mod.inspect_iface(str(tmp_path), "bond0")
    assert info["is_bond"] is True
    assert info["slaves"] == ["eth0", "eth1"]


def test_inspect_bridge(tmp_path):
    _mk_iface(tmp_path, "br0", is_bridge=True,
              bridge_stp_state=1, bridge_ports=["eth0", "eth1"])
    info = mod.inspect_iface(str(tmp_path), "br0")
    assert info["is_bridge"] is True
    assert info["bridge"]["stp_state"] == 1
    assert info["ports"] == ["eth0", "eth1"]


# --- classify ---------------------------------------------------

def test_classify_unknown_empty():
    v = mod.classify(False, [])
    assert v["verdict"] == "unknown"


def test_classify_unknown_present_no_ifaces():
    v = mod.classify(True, [])
    assert v["verdict"] == "unknown"


def _plain(name, **k):
    base = {"name": name, "operstate": "up", "master": None,
            "is_bond": False, "is_bridge": False,
            "lowers": [], "uppers": [], "ports": [],
            "slaves": [], "bonding": {}, "bridge": {}}
    base.update(k)
    return base


def test_classify_ok_no_stacking():
    v = mod.classify(True, [_plain("eth0"), _plain("lo")])
    assert v["verdict"] == "ok"


def test_classify_bond_no_slaves():
    v = mod.classify(True, [
        _plain("bond0", is_bond=True, slaves=[])])
    assert v["verdict"] == "bond_degraded_slave"


def test_classify_bond_degraded():
    v = mod.classify(True, [
        _plain("bond0", is_bond=True,
               slaves=["eth0", "eth1"]),
        _plain("eth0", operstate="up", master="bond0"),
        _plain("eth1", operstate="down", master="bond0"),
    ])
    assert v["verdict"] == "bond_degraded_slave"
    assert "eth1" in v["bad_slaves"]


def test_classify_bond_healthy():
    v = mod.classify(True, [
        _plain("bond0", is_bond=True,
               slaves=["eth0", "eth1"]),
        _plain("eth0", operstate="up", master="bond0"),
        _plain("eth1", operstate="up", master="bond0"),
    ])
    assert v["verdict"] == "ok"


def test_classify_bridge_stp_off_docker_skipped():
    v = mod.classify(True, [
        _plain("docker0", is_bridge=True,
               ports=["veth1", "veth2"],
               bridge={"stp_state": 0}),
    ])
    assert v["verdict"] == "ok"


def test_classify_bridge_stp_off_compose_skipped():
    v = mod.classify(True, [
        _plain("br-abc123def0", is_bridge=True,
               ports=["v1", "v2"],
               bridge={"stp_state": 0}),
    ])
    assert v["verdict"] == "ok"


def test_classify_bridge_stp_off_real():
    v = mod.classify(True, [
        _plain("br0", is_bridge=True,
               ports=["eth0", "wlan0"],
               bridge={"stp_state": 0}),
    ])
    assert v["verdict"] == "bridge_stp_disabled"


def test_classify_bridge_stp_off_single_port_ok():
    # single-port bridge can't loop, don't flag
    v = mod.classify(True, [
        _plain("br0", is_bridge=True, ports=["eth0"],
               bridge={"stp_state": 0}),
    ])
    assert v["verdict"] == "ok"


def test_classify_orphan_lower():
    v = mod.classify(True, [
        _plain("eth0", master="bond_gone"),
    ])
    assert v["verdict"] == "orphan_lower_member"


def test_classify_stacking_inconsistent_missing_lower():
    v = mod.classify(True, [
        _plain("br0", is_bridge=True,
               lowers=["eth_missing"],
               bridge={"stp_state": 1}, ports=[]),
    ])
    assert v["verdict"] == "stacking_inconsistent"


def test_classify_stacking_inconsistent_asymmetry():
    v = mod.classify(True, [
        _plain("br0", is_bridge=True, lowers=["eth0"],
               bridge={"stp_state": 1}, ports=[]),
        _plain("eth0", uppers=[]),
    ])
    assert v["verdict"] == "stacking_inconsistent"


def test_classify_stacking_symmetric_ok():
    v = mod.classify(True, [
        _plain("br0", is_bridge=True, lowers=["eth0"],
               bridge={"stp_state": 1}, ports=["eth0"]),
        _plain("eth0", uppers=["br0"]),
    ])
    assert v["verdict"] == "ok"


# Priority : bond > bridge > orphan > stacking
def test_priority_bond_over_bridge():
    v = mod.classify(True, [
        _plain("bond0", is_bond=True,
               slaves=["eth0"]),
        _plain("eth0", operstate="down", master="bond0"),
        _plain("br0", is_bridge=True,
               ports=["eth1", "eth2"],
               bridge={"stp_state": 0}),
    ])
    assert v["verdict"] == "bond_degraded_slave"


# --- status integration ----------------------------------------

def test_status_unknown(tmp_path):
    out = mod.status(None, str(tmp_path / "nope"))
    assert out["ok"] is False
    assert out["verdict"]["verdict"] == "unknown"


def test_status_ok_plain(tmp_path):
    _mk_iface(tmp_path, "eth0")
    _mk_iface(tmp_path, "lo")
    out = mod.status(None, str(tmp_path))
    assert out["ok"] is True
    assert out["iface_count"] == 2
    assert out["verdict"]["verdict"] == "ok"


def test_status_bond_degraded(tmp_path):
    _mk_iface(tmp_path, "bond0", is_bond=True,
              bond_slaves=["eth0", "eth1"])
    _mk_iface(tmp_path, "eth0", operstate="up", master="bond0")
    _mk_iface(tmp_path, "eth1", operstate="down",
              master="bond0")
    out = mod.status(None, str(tmp_path))
    assert out["bonds"] == ["bond0"]
    assert out["verdict"]["verdict"] == "bond_degraded_slave"
