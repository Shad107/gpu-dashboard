"""Tests for modules/net_proto_counters.py — R&D #44.4."""
from __future__ import annotations

import pytest

from gpu_dashboard.modules import net_proto_counters as mod


SNMP_SAMPLE = """\
Ip: Forwarding DefaultTTL InReceives
Ip: 1 64 6648163
Tcp: RtoAlgorithm RtoMin RtoMax MaxConn ActiveOpens PassiveOpens AttemptFails EstabResets CurrEstab InSegs OutSegs RetransSegs InErrs OutRsts InCsumErrors
Tcp: 1 200 120000 -1 598253 227361 342480 2159 19 7043692 17586768 10537 29 364124 0
Udp: InDatagrams NoPorts InErrors OutDatagrams RcvbufErrors SndbufErrors InCsumErrors IgnoredMulti MemErrors
Udp: 367769 1512 4 201493 0 0 4 144687 0
"""

NETSTAT_SAMPLE = """\
TcpExt: SyncookiesSent ListenOverflows ListenDrops TCPMemoryPressures TCPBacklogDrop PFMemallocDrop TCPAbortOnMemory
TcpExt: 3 408 408 0 0 0 0
"""

SOCKSTAT_SAMPLE = """\
sockets: used 1198
TCP: inuse 21 orphan 0 tw 17 alloc 37 mem 1089
UDP: inuse 12 mem 27
"""


# --- parse_kv_file -------------------------------------------------

def test_parse_kv_file_snmp():
    out = mod.parse_kv_file(SNMP_SAMPLE)
    assert "Tcp" in out
    assert out["Tcp"]["RetransSegs"] == 10537
    assert out["Tcp"]["OutSegs"] == 17586768
    assert out["Udp"]["RcvbufErrors"] == 0


def test_parse_kv_file_netstat():
    out = mod.parse_kv_file(NETSTAT_SAMPLE)
    assert out["TcpExt"]["ListenOverflows"] == 408


def test_parse_kv_file_empty():
    assert mod.parse_kv_file("") == {}


def test_parse_kv_file_mismatched_header_value():
    # Header has 3 fields, value has 2 — skip silently.
    txt = "Tcp: A B C\nTcp: 1 2\n"
    out = mod.parse_kv_file(txt)
    assert out == {}


# --- parse_sockstat ------------------------------------------------

def test_parse_sockstat():
    out = mod.parse_sockstat(SOCKSTAT_SAMPLE)
    assert out["TCP"]["inuse"] == 21
    assert out["TCP"]["tw"] == 17
    assert out["UDP"]["inuse"] == 12


def test_parse_sockstat_empty():
    assert mod.parse_sockstat("") == {}


# --- classify ------------------------------------------------------

def _snmp(tcp=None, udp=None):
    return {"Tcp": tcp or {}, "Udp": udp or {}}


def _netstat(tcp_ext=None):
    return {"TcpExt": tcp_ext or {}}


def test_classify_unknown_when_both_empty():
    v = mod.classify({}, {}, {})
    assert v["verdict"] == "unknown"


def test_classify_ok():
    snmp = _snmp(tcp={"InSegs": 100, "OutSegs": 100,
                        "RetransSegs": 0},
                  udp={"RcvbufErrors": 0})
    v = mod.classify(snmp, _netstat({"ListenOverflows": 0}), {})
    assert v["verdict"] == "ok"


def test_classify_listen_overflow():
    v = mod.classify(_snmp(),
                       _netstat({"ListenOverflows": 408}), {})
    assert v["verdict"] == "listen_overflow"
    assert "somaxconn" in v["recommendation"]


def test_classify_rcvbuf_errors():
    snmp = _snmp(udp={"RcvbufErrors": 42})
    v = mod.classify(snmp, _netstat(), {})
    assert v["verdict"] == "rcvbuf_errors"


def test_classify_high_retrans():
    # 5 % retrans on 1 M out_segs.
    snmp = _snmp(tcp={"RetransSegs": 50_000, "OutSegs": 1_000_000})
    v = mod.classify(snmp, _netstat(), {})
    assert v["verdict"] == "high_retrans"
    assert "5.00 %" in v["reason"]


def test_classify_high_retrans_skipped_below_min_segs():
    # < 100k segs → don't classify.
    snmp = _snmp(tcp={"RetransSegs": 50, "OutSegs": 100})
    v = mod.classify(snmp, _netstat(), {})
    assert v["verdict"] == "ok"


def test_classify_tcp_memory_pressure():
    v = mod.classify(_snmp(),
                       _netstat({"TCPMemoryPressures": 5}), {})
    assert v["verdict"] == "tcp_memory_pressure"


def test_classify_backlog_drops():
    v = mod.classify(_snmp(),
                       _netstat({"TCPBacklogDrop": 100}), {})
    assert v["verdict"] == "backlog_drops"


def test_classify_priority_listen_overflow_wins():
    snmp = _snmp(udp={"RcvbufErrors": 100},
                  tcp={"RetransSegs": 100_000, "OutSegs": 1_000_000})
    v = mod.classify(snmp,
                       _netstat({"ListenOverflows": 1,
                                  "TCPMemoryPressures": 10,
                                  "TCPBacklogDrop": 10}), {})
    assert v["verdict"] == "listen_overflow"


def test_classify_priority_rcvbuf_over_retrans():
    snmp = _snmp(udp={"RcvbufErrors": 1},
                  tcp={"RetransSegs": 100_000, "OutSegs": 1_000_000})
    v = mod.classify(snmp, _netstat(), {})
    assert v["verdict"] == "rcvbuf_errors"


# --- status integration -------------------------------------------

def test_status_with_isolated_files(monkeypatch, tmp_path):
    (tmp_path / "snmp").write_text(SNMP_SAMPLE)
    (tmp_path / "netstat").write_text(NETSTAT_SAMPLE)
    (tmp_path / "sockstat").write_text(SOCKSTAT_SAMPLE)
    monkeypatch.setattr(mod, "_PROC_NET_SNMP", str(tmp_path / "snmp"))
    monkeypatch.setattr(mod, "_PROC_NET_NETSTAT",
                        str(tmp_path / "netstat"))
    monkeypatch.setattr(mod, "_PROC_NET_SOCKSTAT",
                        str(tmp_path / "sockstat"))
    out = mod.status()
    assert out["ok"] is True
    assert out["headline"]["tcp_retrans"] == 10537
    assert out["headline"]["tcp_listen_overflows"] == 408
    assert out["sockstat"]["TCP"]["inuse"] == 21
    # Live sample has 408 ListenOverflows → verdict listen_overflow
    assert out["verdict"]["verdict"] == "listen_overflow"


def test_status_unknown(monkeypatch, tmp_path):
    monkeypatch.setattr(mod, "_PROC_NET_SNMP", str(tmp_path / "nope"))
    monkeypatch.setattr(mod, "_PROC_NET_NETSTAT",
                        str(tmp_path / "nope2"))
    monkeypatch.setattr(mod, "_PROC_NET_SOCKSTAT",
                        str(tmp_path / "nope3"))
    out = mod.status()
    assert out["ok"] is False
    assert out["verdict"]["verdict"] == "unknown"
