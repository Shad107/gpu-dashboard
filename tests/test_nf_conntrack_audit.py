"""Tests for modules/nf_conntrack_audit.py — R&D #45.1."""
from __future__ import annotations

from pathlib import Path

import pytest

from gpu_dashboard.modules import nf_conntrack_audit as mod


STATS_SAMPLE = """\
entries  searched found new invalid ignore delete delete_list insert insert_failed drop early_drop icmp_error expect_new expect_create expect_delete search_restart
00000010 00000000 00000000 00000000 00000000 00000000 00000000 00000000 00000000 00000005 00000003 00000000 00000000 00000000 00000000 00000000 00000000
00000010 00000000 00000000 00000000 00000000 00000000 00000000 00000000 00000000 00000007 00000002 00000000 00000000 00000000 00000000 00000000 00000000
"""


# --- read_sysctls --------------------------------------------------

def test_read_sysctls_missing(tmp_path):
    assert mod.read_sysctls(str(tmp_path / "nope")) == {}


def test_read_sysctls_basic(tmp_path):
    root = tmp_path / "nf"
    root.mkdir()
    (root / "nf_conntrack_max").write_text("262144\n")
    (root / "nf_conntrack_count").write_text("100\n")
    (root / "nf_conntrack_tcp_timeout_time_wait").write_text("120\n")
    out = mod.read_sysctls(str(root))
    assert out["nf_conntrack_max"] == 262144
    assert out["nf_conntrack_count"] == 100


# --- parse_per_cpu_stats ------------------------------------------

def test_parse_per_cpu_stats_basic():
    out = mod.parse_per_cpu_stats(STATS_SAMPLE)
    # insert_failed across both CPUs : 0x5 + 0x7 = 0xc = 12
    assert out["insert_failed"] == 12
    # drop : 0x3 + 0x2 = 5
    assert out["drop"] == 5
    # entries : 0x10 + 0x10 = 0x20 = 32 (sum across CPUs)
    assert out["entries"] == 32


def test_parse_per_cpu_stats_empty():
    assert mod.parse_per_cpu_stats("") == {}


def test_parse_per_cpu_stats_header_only():
    assert mod.parse_per_cpu_stats("entries drop\n") == {}


def test_parse_per_cpu_stats_skips_mismatched_row():
    txt = ("entries drop\n"
           "00000010 00000005\n"
           "01 02 03 04\n"
           "00000010 00000005\n")
    out = mod.parse_per_cpu_stats(txt)
    assert out["entries"] == 0x20
    assert out["drop"] == 0xa


# --- classify ------------------------------------------------------

def test_classify_unknown():
    v = mod.classify({}, {})
    assert v["verdict"] == "unknown"


def test_classify_no_conntrack():
    # sysctls present but no max field (some weird kernel config).
    v = mod.classify({"nf_conntrack_buckets": 1024}, {})
    assert v["verdict"] == "no_conntrack"


def test_classify_ok():
    v = mod.classify({"nf_conntrack_max": 262144,
                       "nf_conntrack_count": 100,
                       "nf_conntrack_tcp_timeout_time_wait": 60}, {})
    assert v["verdict"] == "ok"


def test_classify_insert_drops():
    v = mod.classify({"nf_conntrack_max": 262144,
                       "nf_conntrack_count": 100},
                      {"insert_failed": 100, "drop": 5})
    assert v["verdict"] == "insert_drops"
    assert "105" in v["reason"]


def test_classify_table_saturated():
    v = mod.classify({"nf_conntrack_max": 1000,
                       "nf_conntrack_count": 850,
                       "nf_conntrack_tcp_timeout_time_wait": 60}, {})
    assert v["verdict"] == "table_saturated"
    assert "85" in v["reason"]


def test_classify_time_wait_bloat():
    # 60 % full + TW timeout 120 s → bloat warning.
    v = mod.classify({"nf_conntrack_max": 1000,
                       "nf_conntrack_count": 600,
                       "nf_conntrack_tcp_timeout_time_wait": 120}, {})
    assert v["verdict"] == "time_wait_bloat"
    assert "time_wait" in v["recommendation"]


def test_classify_priority_insert_drops_wins():
    v = mod.classify({"nf_conntrack_max": 1000,
                       "nf_conntrack_count": 950,
                       "nf_conntrack_tcp_timeout_time_wait": 120},
                      {"insert_failed": 1})
    assert v["verdict"] == "insert_drops"


def test_classify_priority_saturated_over_time_wait():
    v = mod.classify({"nf_conntrack_max": 1000,
                       "nf_conntrack_count": 850,
                       "nf_conntrack_tcp_timeout_time_wait": 120}, {})
    assert v["verdict"] == "table_saturated"


# --- status integration -------------------------------------------

def test_status_with_isolated(monkeypatch, tmp_path):
    nf_dir = tmp_path / "nf"
    nf_dir.mkdir()
    (nf_dir / "nf_conntrack_max").write_text("1000\n")
    (nf_dir / "nf_conntrack_count").write_text("850\n")
    (nf_dir / "nf_conntrack_tcp_timeout_time_wait").write_text("60\n")
    monkeypatch.setattr(mod, "_PROC_SYS_NETFILTER", str(nf_dir))
    monkeypatch.setattr(mod, "_PROC_NET_STAT_NF",
                        str(tmp_path / "noproc"))
    out = mod.status()
    assert out["ok"] is True
    assert out["verdict"]["verdict"] == "table_saturated"


def test_status_unknown(monkeypatch, tmp_path):
    monkeypatch.setattr(mod, "_PROC_SYS_NETFILTER",
                        str(tmp_path / "nope"))
    monkeypatch.setattr(mod, "_PROC_NET_STAT_NF",
                        str(tmp_path / "nope2"))
    out = mod.status()
    assert out["ok"] is False
    assert out["verdict"]["verdict"] == "unknown"
