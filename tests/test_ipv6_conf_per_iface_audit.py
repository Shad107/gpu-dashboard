"""Tests for modules/ipv6_conf_per_iface_audit.py — R&D #76.2."""
from __future__ import annotations

import pytest

from gpu_dashboard.modules import ipv6_conf_per_iface_audit as mod


def _mk_iface(root, name, **knobs):
    d = root / name
    d.mkdir(parents=True, exist_ok=True)
    for k, v in knobs.items():
        (d / k).write_text(str(v) + "\n")


def _ifaces_ok():
    """All ifaces with sane defaults."""
    base = {"accept_ra": 1, "autoconf": 1, "forwarding": 0,
              "disable_ipv6": 0, "accept_redirects": 0,
              "use_tempaddr": 2, "addr_gen_mode": 1,
              "router_solicitations": -1,
              "accept_source_route": 0}
    return {
        "all":     dict(base),
        "default": dict(base),
        "lo":      {**base, "accept_redirects": 1,
                       "use_tempaddr": 0},
        "eth0":    dict(base),
    }


# --- list_interfaces -------------------------------------------

def test_list_interfaces_missing(tmp_path):
    assert mod.list_interfaces(str(tmp_path / "nope")) == []


def test_list_interfaces(tmp_path):
    _mk_iface(tmp_path, "all", accept_ra=1)
    _mk_iface(tmp_path, "eth0", accept_ra=1)
    out = mod.list_interfaces(str(tmp_path))
    assert out == ["all", "eth0"]


# --- classify ---------------------------------------------------

def test_classify_unknown():
    v = mod.classify(False, {})
    assert v["verdict"] == "unknown"


def test_classify_ok():
    v = mod.classify(True, _ifaces_ok())
    assert v["verdict"] == "ok"


def test_classify_ra_accept_on_router():
    ifaces = _ifaces_ok()
    ifaces["eth0"]["forwarding"] = 1  # accept_ra already = 1
    v = mod.classify(True, ifaces)
    assert v["verdict"] == "ra_accept_on_router"


def test_classify_tempaddr_disabled():
    ifaces = _ifaces_ok()
    ifaces["eth0"]["use_tempaddr"] = 0
    v = mod.classify(True, ifaces)
    assert v["verdict"] == "tempaddr_disabled_public"


def test_classify_tempaddr_lo_ignored():
    # lo's use_tempaddr=0 in _ifaces_ok() is fine
    v = mod.classify(True, _ifaces_ok())
    assert v["verdict"] == "ok"


def test_classify_unsolicited_forwarding():
    ifaces = _ifaces_ok()
    ifaces["all"]["forwarding"] = 1
    ifaces["all"]["accept_ra"] = 0     # avoid RA-on-router
    ifaces["eth0"]["accept_ra"] = 0
    v = mod.classify(True, ifaces)
    assert v["verdict"] == "unsolicited_forwarding"


def test_classify_redirects_accepted():
    ifaces = _ifaces_ok()
    ifaces["eth0"]["accept_redirects"] = 1
    v = mod.classify(True, ifaces)
    assert v["verdict"] == "redirects_accepted"


# Priority : ra_router > tempaddr > forwarding > redirects
def test_priority_ra_router_over_tempaddr():
    ifaces = _ifaces_ok()
    ifaces["eth0"]["forwarding"] = 1   # accept_ra already 1
    ifaces["eth0"]["use_tempaddr"] = 0
    v = mod.classify(True, ifaces)
    assert v["verdict"] == "ra_accept_on_router"


def test_priority_tempaddr_over_forwarding():
    ifaces = _ifaces_ok()
    ifaces["eth0"]["use_tempaddr"] = 0
    ifaces["all"]["forwarding"] = 1
    ifaces["all"]["accept_ra"] = 0
    ifaces["eth0"]["accept_ra"] = 0
    v = mod.classify(True, ifaces)
    assert v["verdict"] == "tempaddr_disabled_public"


def test_priority_forwarding_over_redirects():
    ifaces = _ifaces_ok()
    ifaces["all"]["forwarding"] = 1
    ifaces["all"]["accept_ra"] = 0
    ifaces["eth0"]["accept_ra"] = 0
    ifaces["eth0"]["accept_redirects"] = 1
    v = mod.classify(True, ifaces)
    assert v["verdict"] == "unsolicited_forwarding"


# --- status integration ----------------------------------------

def test_status_unknown(tmp_path):
    out = mod.status(None, str(tmp_path / "nope"))
    assert out["ok"] is False
    assert out["verdict"]["verdict"] == "unknown"


def test_status_ok_synthetic(tmp_path):
    knobs = {"accept_ra": 1, "autoconf": 1, "forwarding": 0,
                "disable_ipv6": 0, "accept_redirects": 0,
                "use_tempaddr": 2, "addr_gen_mode": 1,
                "router_solicitations": -1,
                "accept_source_route": 0}
    for n in ("all", "default", "lo", "eth0"):
        _mk_iface(tmp_path, n, **knobs)
    out = mod.status(None, str(tmp_path))
    assert out["ok"] is True
    assert out["iface_count"] == 4
    assert out["verdict"]["verdict"] == "ok"


def test_status_redirects_synthetic(tmp_path):
    knobs = {"accept_ra": 1, "autoconf": 1, "forwarding": 0,
                "disable_ipv6": 0, "accept_redirects": 0,
                "use_tempaddr": 2, "addr_gen_mode": 1,
                "router_solicitations": -1,
                "accept_source_route": 0}
    for n in ("all", "lo"):
        _mk_iface(tmp_path, n, **knobs)
    _mk_iface(tmp_path, "eth0",
                  **{**knobs, "accept_redirects": 1})
    out = mod.status(None, str(tmp_path))
    assert out["verdict"]["verdict"] == "redirects_accepted"
