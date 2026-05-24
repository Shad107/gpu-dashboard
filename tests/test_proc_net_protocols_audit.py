"""Tests for modules/proc_net_protocols_audit.py R&D #90.2."""
from __future__ import annotations

import pytest

from gpu_dashboard.modules import proc_net_protocols_audit as mod


_PACKET_HEADER = (
    "sk               RefCnt Type Proto  "
    "Iface R Rmem   User   Inode\n")
_RAW_HEADER = (
    "  sl  local_address rem_address   st tx_queue rx_queue "
    "tr tm->when retrnsmt   uid  timeout inode ref pointer "
    "drops\n")


def _mk_proc_net(tmp_path, *, packet_rows=None,
                  raw4_rows=0, raw6_rows=0):
    d = tmp_path / "proc_net"
    d.mkdir(parents=True, exist_ok=True)
    txt = _PACKET_HEADER
    if packet_rows:
        for r in packet_rows:
            txt += (f"0000000000000000 3 {r.get('type', '3')} "
                     f"{r['proto']} 2 1 0 998 12962\n")
    (d / "packet").write_text(txt)
    raw4 = _RAW_HEADER + "row4\n" * raw4_rows
    raw6 = _RAW_HEADER + "row6\n" * raw6_rows
    (d / "raw").write_text(raw4)
    (d / "raw6").write_text(raw6)
    return str(d)


# --- parse_packet ----------------------------------------------

def test_parse_packet_empty():
    assert mod.parse_packet("") == []


def test_parse_packet_only_header():
    assert mod.parse_packet(_PACKET_HEADER) == []


def test_parse_packet_with_rows():
    text = (_PACKET_HEADER
            + "0000 3 3 88cc 2 1 0 998 12962\n"
            + "0000 3 3 0003 2 1 0 998 12963\n")
    rows = mod.parse_packet(text)
    assert len(rows) == 2
    assert rows[0]["proto"] == "88cc"
    assert rows[1]["proto"] == "0003"


# --- count_raw -------------------------------------------------

def test_count_raw_empty():
    assert mod.count_raw("") == 0


def test_count_raw_only_header():
    assert mod.count_raw(_RAW_HEADER) == 0


def test_count_raw_with_rows():
    text = _RAW_HEADER + "row1\nrow2\nrow3\n"
    assert mod.count_raw(text) == 3


# --- classify --------------------------------------------------

def test_classify_unknown_all_empty():
    v = mod.classify([], 0, 0)
    assert v["verdict"] == "unknown"


def test_classify_ok_normal():
    rows = [{"proto": "88cc"}, {"proto": "88cc"}]
    v = mod.classify(rows, 1, 1)
    assert v["verdict"] == "ok"


def test_classify_af_packet_promisc():
    rows = [{"proto": "0003"}, {"proto": "88cc"}]
    v = mod.classify(rows, 0, 0)
    assert v["verdict"] == "af_packet_promisc_listener"
    assert v["promisc_count"] == 1


def test_classify_raw_socket_leak():
    v = mod.classify([{"proto": "88cc"}], 3, 3)
    assert v["verdict"] == "raw_socket_leak"
    assert v["raw_total"] == 6


def test_classify_raw_at_threshold_is_ok():
    v = mod.classify([{"proto": "88cc"}], 2, 2)
    # threshold is > 4 strict, so 4 is ok
    assert v["verdict"] == "ok"


# Priority : promisc > raw_leak
def test_priority_promisc_over_raw_leak():
    rows = [{"proto": "0003"}]
    v = mod.classify(rows, 5, 5)
    assert v["verdict"] == "af_packet_promisc_listener"


# --- status integration ----------------------------------------

def test_status_unknown(tmp_path):
    out = mod.status(None, str(tmp_path / "nope"))
    assert out["verdict"]["verdict"] == "unknown"


def test_status_ok_synthetic(tmp_path):
    r = _mk_proc_net(tmp_path,
                          packet_rows=[{"proto": "88cc"}],
                          raw4_rows=0, raw6_rows=1)
    out = mod.status(None, r)
    assert out["verdict"]["verdict"] == "ok"
    assert out["packet_socket_count"] == 1
    assert out["raw_socket_count"] == 1


def test_status_promisc_synthetic(tmp_path):
    r = _mk_proc_net(tmp_path,
                          packet_rows=[{"proto": "0003"}])
    out = mod.status(None, r)
    assert (out["verdict"]["verdict"]
            == "af_packet_promisc_listener")
    assert out["ok"] is False


def test_status_raw_leak_synthetic(tmp_path):
    r = _mk_proc_net(tmp_path,
                          packet_rows=[{"proto": "88cc"}],
                          raw4_rows=5, raw6_rows=2)
    out = mod.status(None, r)
    assert out["verdict"]["verdict"] == "raw_socket_leak"
    assert out["raw_socket_count"] == 7
