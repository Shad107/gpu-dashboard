"""Tests for modules/snmp6_icmp_audit.py — R&D #80.2."""
from __future__ import annotations

import pytest

from gpu_dashboard.modules import snmp6_icmp_audit as mod


def _snmp6(**counters):
    """Build /proc/net/snmp6 text from kwargs."""
    return "\n".join(
        f"{k}\t{v}" for k, v in counters.items()) + "\n"


# --- parse -----------------------------------------------------

def test_parse_empty():
    assert mod.parse_snmp6("") == {}


def test_parse_basic():
    out = mod.parse_snmp6(
        "Icmp6InMsgs\t100\nIcmp6InErrors\t0\n")
    assert out["Icmp6InMsgs"] == 100
    assert out["Icmp6InErrors"] == 0


def test_parse_skips_garbage():
    out = mod.parse_snmp6(
        "Icmp6InMsgs\t100\ngarbage_line\nValid\t42\n")
    assert out == {"Icmp6InMsgs": 100, "Valid": 42}


# --- read_snmp6 ------------------------------------------------

def test_read_missing(tmp_path):
    assert mod.read_snmp6(str(tmp_path / "nope")) is None


def test_read_ok(tmp_path):
    p = tmp_path / "snmp6"
    p.write_text("Icmp6InMsgs\t100\n")
    assert mod.read_snmp6(str(p)) == {"Icmp6InMsgs": 100}


# --- classify --------------------------------------------------

def test_classify_unknown_none():
    v = mod.classify(None)
    assert v["verdict"] == "unknown"


def test_classify_unknown_empty():
    v = mod.classify({})
    assert v["verdict"] == "unknown"


def _ok_counters():
    return {
        "Icmp6InMsgs": 10000,
        "Icmp6InErrors": 0,
        "Icmp6InCsumErrors": 0,
        "Icmp6OutMsgs": 5000,
        "Icmp6OutErrors": 0,
        "Icmp6InNeighborAdvertisements": 100,
        "Icmp6OutNeighborSolicits": 100,
        "Ip6InAddrErrors": 0,
        "Ip6InHdrErrors": 0,
        "Ip6InReceives": 50000,
        "Ip6OutRequests": 30000,
    }


def test_classify_ok():
    v = mod.classify(_ok_counters())
    assert v["verdict"] == "ok"


def test_classify_csum_errors():
    c = _ok_counters()
    c["Icmp6InCsumErrors"] = 5
    v = mod.classify(c)
    assert v["verdict"] == "icmp6_in_errors_growing"
    assert v["csum_errors"] == 5


def test_classify_in_errors_high_ratio():
    c = _ok_counters()
    c["Icmp6InMsgs"] = 5000
    c["Icmp6InErrors"] = 200  # 4 % ratio
    v = mod.classify(c)
    assert v["verdict"] == "icmp6_in_errors_growing"


def test_classify_in_errors_below_floor_ok():
    c = _ok_counters()
    c["Icmp6InErrors"] = 50  # below floor 100
    v = mod.classify(c)
    assert v["verdict"] == "ok"


def test_classify_in_errors_low_ratio_ok():
    c = _ok_counters()
    c["Icmp6InMsgs"] = 1_000_000
    c["Icmp6InErrors"] = 500  # 0.05 % — below ratio
    v = mod.classify(c)
    assert v["verdict"] == "ok"


def test_classify_na_storm():
    c = _ok_counters()
    c["Icmp6InNeighborAdvertisements"] = 100_000
    c["Icmp6OutNeighborSolicits"] = 10
    v = mod.classify(c)
    assert v["verdict"] == "nd_unsolicited_advert_storm"


def test_classify_na_below_floor_ok():
    c = _ok_counters()
    c["Icmp6InNeighborAdvertisements"] = 1000
    c["Icmp6OutNeighborSolicits"] = 10
    v = mod.classify(c)
    assert v["verdict"] == "ok"


def test_classify_addr_errors():
    c = _ok_counters()
    c["Ip6InAddrErrors"] = 200
    v = mod.classify(c)
    assert v["verdict"] == "mld_query_loss"


def test_classify_hdr_errors():
    c = _ok_counters()
    c["Ip6InHdrErrors"] = 5
    v = mod.classify(c)
    assert v["verdict"] == "mld_query_loss"


# Priority : csum > in_err > na_storm > addr_err
def test_priority_csum_over_na_storm():
    c = _ok_counters()
    c["Icmp6InCsumErrors"] = 5
    c["Icmp6InNeighborAdvertisements"] = 100_000
    c["Icmp6OutNeighborSolicits"] = 10
    v = mod.classify(c)
    assert v["verdict"] == "icmp6_in_errors_growing"


def test_priority_na_over_addr():
    c = _ok_counters()
    c["Icmp6InNeighborAdvertisements"] = 100_000
    c["Icmp6OutNeighborSolicits"] = 10
    c["Ip6InAddrErrors"] = 200
    v = mod.classify(c)
    assert v["verdict"] == "nd_unsolicited_advert_storm"


# --- status integration ----------------------------------------

def test_status_unknown(tmp_path):
    out = mod.status(None, str(tmp_path / "nope"))
    assert out["ok"] is False
    assert out["verdict"]["verdict"] == "unknown"


def test_status_ok(tmp_path):
    p = tmp_path / "snmp6"
    p.write_text(_snmp6(**_ok_counters()))
    out = mod.status(None, str(p))
    assert out["ok"] is True
    assert out["sample"]["Icmp6InMsgs"] == 10000
    assert out["verdict"]["verdict"] == "ok"


def test_status_csum_err(tmp_path):
    p = tmp_path / "snmp6"
    c = _ok_counters()
    c["Icmp6InCsumErrors"] = 7
    p.write_text(_snmp6(**c))
    out = mod.status(None, str(p))
    assert out["ok"] is False
    assert out["verdict"]["verdict"] == "icmp6_in_errors_growing"
