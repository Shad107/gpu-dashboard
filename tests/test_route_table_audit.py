"""Tests for modules/route_table_audit.py — R&D #79.2."""
from __future__ import annotations

import pytest

from gpu_dashboard.modules import route_table_audit as mod

HEADER = (
    "Iface\tDestination\tGateway\tFlags\tRefCnt\tUse\t"
    "Metric\tMask\tMTU\tWindow\tIRTT\n")


def _v4_line(iface, dest, gw, mask, flags="0003", metric=100):
    return (f"{iface}\t{dest}\t{gw}\t{flags}\t0\t0\t"
            f"{metric}\t{mask}\t0\t0\t0\n")


# --- _parse_le_ip4 ---------------------------------------------

def test_parse_le_ip4_default():
    assert mod._parse_le_ip4("00000000") == "0.0.0.0"


def test_parse_le_ip4_normal():
    # FE01A8C0 little-endian → 192.168.1.254
    assert mod._parse_le_ip4("FE01A8C0") == "192.168.1.254"


def test_parse_le_ip4_bad_len():
    assert mod._parse_le_ip4("ABC") is None


# --- _parse_ip6 ------------------------------------------------

def test_parse_ip6_default():
    assert mod._parse_ip6("0" * 32) == ":".join(["0000"] * 8)


def test_parse_ip6_normal():
    out = mod._parse_ip6(
        "2a010e0a0ff789000000000000000000")
    assert out == "2a01:0e0a:0ff7:8900:0000:0000:0000:0000"


# --- parse_v4 --------------------------------------------------

def test_parse_v4_empty():
    assert mod.parse_v4("") == []


def test_parse_v4_real():
    text = HEADER + _v4_line(
        "ens18", "00000000", "FE01A8C0", "00000000")
    rows = mod.parse_v4(text)
    assert len(rows) == 1
    assert rows[0]["iface"] == "ens18"
    assert rows[0]["destination"] == "0.0.0.0"
    assert rows[0]["gateway"] == "192.168.1.254"
    assert mod._is_default_v4(rows[0]) is True


def test_parse_v4_host_route():
    text = HEADER + _v4_line(
        "tun0", "01020304", "00000000", "FFFFFFFF")
    rows = mod.parse_v4(text)
    assert mod._is_host_route_v4(rows[0]) is True


# --- classify --------------------------------------------------

def test_classify_unknown():
    v = mod.classify(None, None)
    assert v["verdict"] == "unknown"


def _default(iface, metric=100):
    return {"iface": iface, "destination_raw": "00000000",
            "destination": "0.0.0.0", "gateway_raw": "FE01A8C0",
            "gateway": "192.168.1.254", "flags": 0x3,
            "metric": metric, "mask": "00000000",
            "mask_decoded": "0.0.0.0"}


def _host(iface):
    return {"iface": iface, "destination_raw": "01020304",
            "destination": "4.3.2.1", "gateway_raw": "00000000",
            "gateway": "0.0.0.0", "flags": 0x5,
            "metric": 0, "mask": "FFFFFFFF",
            "mask_decoded": "255.255.255.255"}


def test_classify_no_default():
    v = mod.classify([], None)
    assert v["verdict"] == "err"
    assert "No IPv4 default" in v["reason"]


def test_classify_conflicting_defaults():
    v = mod.classify([
        _default("ens18", metric=100),
        _default("eth1", metric=100),
    ], None)
    assert v["verdict"] == "err"
    assert v["conflicting_ifaces"] == ["ens18", "eth1"]


def test_classify_two_defaults_different_metrics_ok():
    v = mod.classify([
        _default("ens18", metric=100),
        _default("eth1", metric=200),
    ], None)
    assert v["verdict"] == "ok"


def test_classify_container_default():
    v = mod.classify([_default("docker0")], None)
    assert v["verdict"] == "warn"
    assert v["iface"] == "docker0"


def test_classify_virbr_default():
    v = mod.classify([_default("virbr0")], None)
    assert v["verdict"] == "warn"


def test_classify_wg_default_ok():
    # wg0 (WireGuard) default IS legit — don't flag
    v = mod.classify([_default("wg0")], None)
    assert v["verdict"] == "ok"


def test_classify_tun_default_ok():
    v = mod.classify([_default("tun0")], None)
    assert v["verdict"] == "ok"


def test_classify_many_defaults():
    v = mod.classify([
        _default("ens18", metric=100),
        _default("eth1", metric=200),
        _default("eth2", metric=300),
    ], None)
    assert v["verdict"] == "warn"
    assert v["default_count"] == 3


def test_classify_many_host_routes():
    rows = [_default("ens18")] + [_host("ens18")
                                       for _ in range(55)]
    v = mod.classify(rows, None)
    assert v["verdict"] == "accent"
    assert v["host_route_count"] == 55


def test_classify_few_host_routes_ok():
    rows = [_default("ens18")] + [_host("ens18")
                                       for _ in range(10)]
    v = mod.classify(rows, None)
    assert v["verdict"] == "ok"


def test_classify_ok():
    v = mod.classify([_default("ens18")], None)
    assert v["verdict"] == "ok"
    assert v["default_iface"] == "ens18"
    assert v["default_gateway"] == "192.168.1.254"


# Priority : no_default > conflict > container > many > host
def test_priority_conflict_over_container():
    v = mod.classify([
        _default("ens18", metric=100),
        _default("docker0", metric=100),
    ], None)
    # both same metric on diff ifaces → conflict err
    assert v["verdict"] == "err"


def test_priority_container_over_host_count():
    rows = [_default("docker0")] + [_host("ens18")
                                          for _ in range(60)]
    v = mod.classify(rows, None)
    assert v["verdict"] == "warn"


# --- status integration ----------------------------------------

def test_status_unknown(tmp_path):
    out = mod.status(None,
                     str(tmp_path / "nope_v4"),
                     str(tmp_path / "nope_v6"))
    assert out["ok"] is False
    assert out["verdict"]["verdict"] == "unknown"


def test_status_ok(tmp_path):
    v4 = tmp_path / "route"
    v4.write_text(HEADER + _v4_line(
        "ens18", "00000000", "FE01A8C0", "00000000"))
    v6 = tmp_path / "ipv6_route"
    v6.write_text("")
    out = mod.status(None, str(v4), str(v6))
    assert out["ok"] is True
    assert out["default_v4_count"] == 1
    assert out["verdict"]["verdict"] == "ok"


def test_status_err_no_default(tmp_path):
    v4 = tmp_path / "route"
    # only a host route, no default
    v4.write_text(HEADER + _v4_line(
        "ens18", "01020304", "00000000", "FFFFFFFF"))
    v6 = tmp_path / "ipv6_route"
    v6.write_text("")
    out = mod.status(None, str(v4), str(v6))
    assert out["ok"] is False
    assert out["verdict"]["verdict"] == "err"
