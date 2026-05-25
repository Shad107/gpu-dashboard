"""Tests for modules/ephemeral_port_range_audit.py R&D #103.2."""
from __future__ import annotations

import pytest

from gpu_dashboard.modules import ephemeral_port_range_audit as mod


# --- parse_port_range ------------------------------------------

def test_parse_port_range_empty():
    assert mod.parse_port_range("") is None
    assert mod.parse_port_range(None) is None


def test_parse_port_range_basic():
    assert mod.parse_port_range(
        "32768\t60999\n") == (32768, 60999)


def test_parse_port_range_malformed():
    assert mod.parse_port_range("garbage") is None


# --- count_tcp_sockets -----------------------------------------

def test_count_tcp_empty():
    assert mod.count_tcp_sockets("") == 0


def test_count_tcp_header_only():
    assert mod.count_tcp_sockets(
        "  sl  local_address rem_address ...\n") == 0


def test_count_tcp_basic():
    text = (
        "  sl  local_address rem_address st tx_queue rx_queue\n"
        "   0: 0100007F:1F90 00000000:0000 0A ...\n"
        "   1: 0100007F:1F91 00000000:0000 0A ...\n")
    assert mod.count_tcp_sockets(text) == 2


# --- classify --------------------------------------------------

def test_classify_unknown():
    v = mod.classify(False, None, None, False, 0)
    assert v["verdict"] == "unknown"


def test_classify_requires_root():
    v = mod.classify(True, None, None, False, 0)
    assert v["verdict"] == "requires_root"


def test_classify_ok():
    v = mod.classify(True, (32768, 60999),
                          1024, True, 100)
    assert v["verdict"] == "ok"


def test_classify_pool_exhausted_err():
    # window=100, socket count=90 → 90% used
    v = mod.classify(True, (50000, 50099),
                          1024, True, 90)
    assert v["verdict"] == "ephemeral_pool_exhausted"


def test_classify_window_too_small_warn():
    v = mod.classify(True, (50000, 60000),
                          1024, True, 100)
    assert v["verdict"] == "port_window_too_small"


def test_classify_unpriv_low_accent():
    v = mod.classify(True, (32768, 60999),
                          80, True, 10)
    assert v["verdict"] == "unpriv_port_below_1024"


def test_classify_unpriv_1024_is_ok():
    # 1024 is the default — not flagged
    v = mod.classify(True, (32768, 60999),
                          1024, True, 10)
    assert v["verdict"] == "ok"


# Priority : exhausted > window > unpriv
def test_priority_exhausted_over_window():
    v = mod.classify(True, (50000, 50099),
                          80, True, 90)
    assert v["verdict"] == "ephemeral_pool_exhausted"


def test_priority_window_over_unpriv():
    v = mod.classify(True, (50000, 50500),
                          80, True, 100)
    # 501 ports < 16384 → window warn beats unpriv accent
    assert v["verdict"] == "port_window_too_small"


# --- status integration ----------------------------------------

def test_status_unknown(tmp_path):
    out = mod.status(None,
                       str(tmp_path / "no_ipv4"),
                       str(tmp_path / "no_tcp"),
                       str(tmp_path / "no_tcp6"))
    assert out["verdict"]["verdict"] == "unknown"


def test_status_ok_synthetic(tmp_path):
    d = tmp_path / "ipv4"
    d.mkdir()
    (d / "ip_local_port_range").write_text(
        "32768\t60999\n")
    (d / "ip_unprivileged_port_start").write_text("1024\n")
    (d / "ip_local_reserved_ports").write_text("\n")
    tcp = tmp_path / "tcp"
    tcp.write_text(
        "  sl local rem st\n   0: 0100007F:1F90 00:0 0A\n")
    tcp6 = tmp_path / "tcp6"
    tcp6.write_text("  sl local rem st\n")
    out = mod.status(None, str(d), str(tcp), str(tcp6))
    assert out["verdict"]["verdict"] == "ok"
    assert out["port_window"] == 28232
    assert out["tcp_socket_count"] == 1
