"""Tests for modules/ipv4_conf_per_iface_audit.py — R&D #75.4."""
from __future__ import annotations

import pytest

from gpu_dashboard.modules import ipv4_conf_per_iface_audit as mod


def _mk_iface(root, name, **knobs):
    d = root / name
    d.mkdir(parents=True, exist_ok=True)
    for k, v in knobs.items():
        (d / k).write_text(str(v) + "\n")


# --- list_interfaces -------------------------------------------

def test_list_interfaces_missing(tmp_path):
    assert mod.list_interfaces(str(tmp_path / "nope")) == []


def test_list_interfaces(tmp_path):
    _mk_iface(tmp_path, "all")
    _mk_iface(tmp_path, "default")
    _mk_iface(tmp_path, "eth0")
    _mk_iface(tmp_path, "lo")
    out = mod.list_interfaces(str(tmp_path))
    assert out == ["all", "default", "eth0", "lo"]


# --- read_iface_knobs ------------------------------------------

def test_read_iface_knobs(tmp_path):
    _mk_iface(tmp_path, "eth0",
                  rp_filter=1, accept_redirects=0,
                  accept_source_route=0, send_redirects=1,
                  log_martians=0, arp_ignore=0,
                  arp_announce=0, forwarding=0)
    out = mod.read_iface_knobs(str(tmp_path), "eth0")
    assert out["rp_filter"] == 1
    assert out["forwarding"] == 0


# --- classify ---------------------------------------------------

def _ifaces_ok():
    """All ifaces with sane defaults."""
    return {
        "all":     {"rp_filter": 1, "accept_redirects": 0,
                       "accept_source_route": 0,
                       "send_redirects": 1, "log_martians": 0,
                       "arp_ignore": 0, "arp_announce": 0,
                       "forwarding": 0},
        "default": {"rp_filter": 1, "accept_redirects": 0,
                       "accept_source_route": 0,
                       "send_redirects": 1, "log_martians": 0,
                       "arp_ignore": 0, "arp_announce": 0,
                       "forwarding": 0},
        "lo":      {"rp_filter": 0, "accept_redirects": 1,
                       "accept_source_route": 0,
                       "send_redirects": 0, "log_martians": 0,
                       "arp_ignore": 0, "arp_announce": 0,
                       "forwarding": 0},
        "eth0":    {"rp_filter": 1, "accept_redirects": 0,
                       "accept_source_route": 0,
                       "send_redirects": 1, "log_martians": 0,
                       "arp_ignore": 0, "arp_announce": 0,
                       "forwarding": 0},
    }


def test_classify_unknown():
    v = mod.classify(False, {})
    assert v["verdict"] == "unknown"


def test_classify_ok():
    v = mod.classify(True, _ifaces_ok())
    assert v["verdict"] == "ok"


def test_classify_source_route():
    ifaces = _ifaces_ok()
    ifaces["eth0"]["accept_source_route"] = 1
    v = mod.classify(True, ifaces)
    assert v["verdict"] == "source_route_accepted"


def test_classify_rp_filter_zero():
    ifaces = _ifaces_ok()
    ifaces["eth0"]["rp_filter"] = 0
    v = mod.classify(True, ifaces)
    assert v["verdict"] == "rp_filter_loose"


def test_classify_rp_filter_loose_value_2_ok():
    # rp_filter=2 (loose RFC 3704) is NOT flagged.
    ifaces = _ifaces_ok()
    ifaces["eth0"]["rp_filter"] = 2
    v = mod.classify(True, ifaces)
    assert v["verdict"] == "ok"


def test_classify_redirects_accepted():
    ifaces = _ifaces_ok()
    ifaces["eth0"]["accept_redirects"] = 1
    v = mod.classify(True, ifaces)
    assert v["verdict"] == "redirects_accepted"


def test_classify_redirects_lo_ignored():
    # lo's accept_redirects=1 is normal.
    ifaces = _ifaces_ok()  # lo already has accept_redirects=1
    v = mod.classify(True, ifaces)
    assert v["verdict"] == "ok"


def test_classify_forwarding_unexpected():
    ifaces = _ifaces_ok()
    ifaces["all"]["forwarding"] = 1
    v = mod.classify(True, ifaces)
    assert v["verdict"] == "forwarding_unexpected"


# Priority : source_route > rp_filter_zero > redirects > forwarding
def test_priority_source_route_over_rp_zero():
    ifaces = _ifaces_ok()
    ifaces["eth0"]["rp_filter"] = 0
    ifaces["eth0"]["accept_source_route"] = 1
    v = mod.classify(True, ifaces)
    assert v["verdict"] == "source_route_accepted"


def test_priority_rp_zero_over_redirects():
    ifaces = _ifaces_ok()
    ifaces["eth0"]["rp_filter"] = 0
    ifaces["eth0"]["accept_redirects"] = 1
    v = mod.classify(True, ifaces)
    assert v["verdict"] == "rp_filter_loose"


def test_priority_redirects_over_forwarding():
    ifaces = _ifaces_ok()
    ifaces["eth0"]["accept_redirects"] = 1
    ifaces["all"]["forwarding"] = 1
    v = mod.classify(True, ifaces)
    assert v["verdict"] == "redirects_accepted"


# --- status integration ----------------------------------------

def test_status_unknown(tmp_path):
    out = mod.status(None, str(tmp_path / "nope"))
    assert out["ok"] is False
    assert out["verdict"]["verdict"] == "unknown"


def test_status_ok_synthetic(tmp_path):
    for n in ("all", "default", "lo", "eth0"):
        _mk_iface(tmp_path, n,
                       rp_filter=1, accept_redirects=0,
                       accept_source_route=0,
                       send_redirects=1, log_martians=0,
                       arp_ignore=0, arp_announce=0,
                       forwarding=0)
    out = mod.status(None, str(tmp_path))
    assert out["ok"] is True
    assert out["iface_count"] == 4
    assert out["verdict"]["verdict"] == "ok"


def test_status_forwarding_synthetic(tmp_path):
    _mk_iface(tmp_path, "all",
                  rp_filter=1, accept_redirects=0,
                  accept_source_route=0, send_redirects=1,
                  log_martians=0, arp_ignore=0,
                  arp_announce=0, forwarding=1)
    _mk_iface(tmp_path, "eth0",
                  rp_filter=1, accept_redirects=0,
                  accept_source_route=0, send_redirects=1,
                  log_martians=0, arp_ignore=0,
                  arp_announce=0, forwarding=0)
    out = mod.status(None, str(tmp_path))
    assert out["verdict"]["verdict"] == "forwarding_unexpected"
