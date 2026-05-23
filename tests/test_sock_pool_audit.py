"""Tests for modules/sock_pool_audit.py — R&D #50.4."""
from __future__ import annotations

import pytest

from gpu_dashboard.modules import sock_pool_audit as mod


SOCKSTAT_SAMPLE = """\
sockets: used 1030
TCP: inuse 19 orphan 0 tw 18 alloc 28 mem 290
UDP: inuse 12 mem 441
UDPLITE: inuse 0
RAW: inuse 0
FRAG: inuse 0 memory 0
"""


# --- parse_sockstat ----------------------------------------------

def test_parse_sockstat():
    out = mod.parse_sockstat(SOCKSTAT_SAMPLE)
    assert out["TCP"]["inuse"] == 19
    assert out["TCP"]["tw"] == 18
    assert out["UDP"]["inuse"] == 12


def test_parse_sockstat_empty():
    assert mod.parse_sockstat("") == {}
    assert mod.parse_sockstat(None) == {}


# --- count_lines_minus_header ------------------------------------

def test_count_lines(tmp_path):
    p = tmp_path / "f"
    p.write_text("header\nrow1\nrow2\nrow3\n")
    assert mod.count_lines_minus_header(str(p)) == 3


def test_count_lines_empty_file(tmp_path):
    p = tmp_path / "f"
    p.write_text("")
    assert mod.count_lines_minus_header(str(p)) == 0


def test_count_lines_missing(tmp_path):
    assert mod.count_lines_minus_header(str(tmp_path / "nope")) == 0


# --- classify ----------------------------------------------------

def _ss(inuse=19, orphan=0, tw=18, alloc=28):
    return {"TCP": {"inuse": inuse, "orphan": orphan, "tw": tw,
                       "alloc": alloc},
              "UDP": {"inuse": 12}}


def test_classify_unknown():
    v = mod.classify({}, {}, 0, 0, 0, None)
    assert v["verdict"] == "unknown"


def test_classify_ok():
    v = mod.classify(_ss(), {}, 30, 15, 900, 65536)
    assert v["verdict"] == "ok"


def test_classify_time_wait_high_absolute():
    v = mod.classify(_ss(tw=15000), {}, 100, 50, 1000, 65536)
    assert v["verdict"] == "time_wait_high"


def test_classify_time_wait_high_ratio():
    v = mod.classify(_ss(tw=900), {}, 100, 50, 1000,
                       tw_buckets_max=1000)
    assert v["verdict"] == "time_wait_high"


def test_classify_orphan_high():
    v = mod.classify(_ss(orphan=200), {}, 100, 50, 1000, 65536)
    assert v["verdict"] == "orphan_high"


def test_classify_unix_backlog():
    v = mod.classify(_ss(), {}, 100, 50, 6000, 65536)
    assert v["verdict"] == "unix_backlog"


def test_classify_priority_tw_wins():
    v = mod.classify(_ss(tw=15000, orphan=200),
                       {}, 100, 50, 6000, 65536)
    assert v["verdict"] == "time_wait_high"


def test_classify_priority_orphan_over_unix():
    v = mod.classify(_ss(orphan=200), {}, 100, 50, 6000, 65536)
    assert v["verdict"] == "orphan_high"


# --- status integration ------------------------------------------

def test_status_with_isolated(monkeypatch, tmp_path):
    (tmp_path / "sockstat").write_text(SOCKSTAT_SAMPLE)
    (tmp_path / "sockstat6").write_text("TCP6: inuse 9\n")
    (tmp_path / "tcp").write_text("hdr\nrow1\nrow2\n")
    (tmp_path / "tcp6").write_text("hdr\n")
    (tmp_path / "unix").write_text("hdr\n" + "row\n" * 100)
    (tmp_path / "tw_buckets").write_text("65536\n")
    monkeypatch.setattr(mod, "_PROC_NET_SOCKSTAT",
                        str(tmp_path / "sockstat"))
    monkeypatch.setattr(mod, "_PROC_NET_SOCKSTAT6",
                        str(tmp_path / "sockstat6"))
    monkeypatch.setattr(mod, "_PROC_NET_TCP", str(tmp_path / "tcp"))
    monkeypatch.setattr(mod, "_PROC_NET_TCP6",
                        str(tmp_path / "tcp6"))
    monkeypatch.setattr(mod, "_PROC_NET_UNIX",
                        str(tmp_path / "unix"))
    monkeypatch.setattr(mod, "_PROC_SYS_TW_BUCKETS",
                        str(tmp_path / "tw_buckets"))
    out = mod.status()
    assert out["ok"] is True
    assert out["sockstat"]["TCP"]["tw"] == 18
    assert out["tcp_socket_count"] == 2
    assert out["unix_socket_count"] == 100
    assert out["verdict"]["verdict"] == "ok"


def test_status_unknown(monkeypatch, tmp_path):
    for attr in ("_PROC_NET_SOCKSTAT", "_PROC_NET_SOCKSTAT6",
                  "_PROC_NET_TCP", "_PROC_NET_TCP6",
                  "_PROC_NET_UNIX", "_PROC_SYS_TW_BUCKETS"):
        monkeypatch.setattr(mod, attr, str(tmp_path / "nope"))
    out = mod.status()
    assert out["ok"] is False
    assert out["verdict"]["verdict"] == "unknown"
