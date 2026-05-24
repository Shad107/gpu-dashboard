"""Tests for modules/arp_neighbor_audit.py — R&D #80.1."""
from __future__ import annotations

import pytest

from gpu_dashboard.modules import arp_neighbor_audit as mod


ARP_HEADER = ("IP address       HW type     Flags       "
              "HW address            Mask     Device\n")


def _arp_line(ip, hw, flags="0x2", iface="ens18"):
    return (f"{ip}    0x1         {flags}         "
            f"{hw}     *        {iface}\n")


def _stat_text(*per_cpu_rows):
    """First arg is header, then per-CPU value rows."""
    header = ("entries  allocs   destroys hash_grows lookups  "
              "hits     res_failed rcv_probes_mcast "
              "rcv_probes_ucast periodic_gc_runs forced_gc_runs "
              "unresolved_discards table_fulls\n")
    body = "".join(per_cpu_rows)
    return header + body


def _stat_row(table_fulls=0):
    # 13 columns, all hex
    cols = ["00000000"] * 13
    cols[12] = f"{table_fulls:08x}"
    return " ".join(cols) + "\n"


# --- parse_arp -------------------------------------------------

def test_parse_arp_empty():
    assert mod.parse_arp(ARP_HEADER) == []


def test_parse_arp_complete():
    rows = mod.parse_arp(
        ARP_HEADER + _arp_line("1.2.3.4", "aa:bb:cc:dd:ee:ff"))
    assert len(rows) == 1
    assert rows[0]["ip"] == "1.2.3.4"
    assert rows[0]["complete"] is True
    assert rows[0]["iface"] == "ens18"


def test_parse_arp_incomplete():
    # Flags = 0x0 = INCOMPLETE
    rows = mod.parse_arp(ARP_HEADER + _arp_line(
        "1.2.3.5", "00:00:00:00:00:00", flags="0x0"))
    assert rows[0]["complete"] is False


# --- parse_arp_stat --------------------------------------------

def test_parse_arp_stat_aggregates():
    t = _stat_text(_stat_row(table_fulls=2),
                     _stat_row(table_fulls=3))
    out = mod.parse_arp_stat(t)
    assert out["table_fulls"] == 5
    assert out["entries"] == 0


def test_parse_arp_stat_empty():
    assert mod.parse_arp_stat("") is None


# --- read_neigh_thresholds -------------------------------------

def test_read_thresholds(tmp_path):
    (tmp_path / "gc_thresh1").write_text("128\n")
    (tmp_path / "gc_thresh2").write_text("512\n")
    (tmp_path / "gc_thresh3").write_text("1024\n")
    out = mod.read_neigh_thresholds(str(tmp_path))
    assert out == {"gc_thresh1": 128, "gc_thresh2": 512,
                     "gc_thresh3": 1024}


def test_read_thresholds_missing(tmp_path):
    out = mod.read_neigh_thresholds(str(tmp_path / "nope"))
    assert all(v is None for v in out.values())


# --- classify --------------------------------------------------

def test_classify_unknown():
    v = mod.classify(None, None, {})
    assert v["verdict"] == "unknown"


def _arp_row(ip="1.2.3.4", complete=True):
    return {"ip": ip, "hw_type": "0x1",
            "flags": 0x2 if complete else 0x0,
            "hw": "aa:bb:cc:dd:ee:ff", "iface": "ens18",
            "complete": complete}


def test_classify_ok():
    v = mod.classify([_arp_row(), _arp_row("1.2.3.5")],
                       {"table_fulls": 0},
                       {"gc_thresh2": 512, "gc_thresh3": 1024})
    assert v["verdict"] == "ok"


def test_classify_overflow_table_fulls():
    v = mod.classify([_arp_row()],
                       {"table_fulls": 3},
                       {"gc_thresh3": 1024})
    assert v["verdict"] == "arp_table_overflow"


def test_classify_overflow_at_thresh3():
    v = mod.classify([_arp_row() for _ in range(1024)],
                       {"table_fulls": 0},
                       {"gc_thresh3": 1024})
    assert v["verdict"] == "arp_table_overflow"


def test_classify_incomplete_high():
    rows = [_arp_row(f"1.2.3.{i}", complete=False)
            for i in range(1, 8)]
    v = mod.classify(rows, {"table_fulls": 0},
                       {"gc_thresh3": 1024})
    assert v["verdict"] == "incomplete_neighbors_high"
    assert v["incomplete_count"] == 7


def test_classify_incomplete_few_ok():
    rows = ([_arp_row()] * 5
              + [_arp_row(complete=False) for _ in range(3)])
    v = mod.classify(rows, {"table_fulls": 0},
                       {"gc_thresh3": 1024})
    assert v["verdict"] == "ok"


def test_classify_watermark():
    # 410 entries vs gc_thresh2 = 512 → 80.07 % → accent
    rows = [_arp_row(f"1.2.{i}.{j}") for i in range(2)
              for j in range(205)]
    v = mod.classify(rows, {"table_fulls": 0},
                       {"gc_thresh2": 512, "gc_thresh3": 1024})
    assert v["verdict"] == "arp_table_high_watermark"


# Priority : overflow > incomplete > watermark
def test_priority_overflow_over_incomplete():
    rows = [_arp_row(f"1.2.3.{i}", complete=False)
            for i in range(10)]
    v = mod.classify(rows, {"table_fulls": 5},
                       {"gc_thresh3": 1024})
    assert v["verdict"] == "arp_table_overflow"


# --- status integration ----------------------------------------

def test_status_unknown(tmp_path):
    out = mod.status(None,
                       str(tmp_path / "nope_arp"),
                       str(tmp_path / "nope_stat"),
                       str(tmp_path / "nope_neigh"))
    assert out["ok"] is False
    assert out["verdict"]["verdict"] == "unknown"


def test_status_ok(tmp_path):
    arp = tmp_path / "arp"
    arp.write_text(
        ARP_HEADER + _arp_line("1.2.3.4", "aa:bb:cc:dd:ee:ff"))
    stat = tmp_path / "stat"
    stat.write_text(_stat_text(_stat_row()))
    neigh = tmp_path / "neigh"
    neigh.mkdir()
    (neigh / "gc_thresh3").write_text("1024\n")
    out = mod.status(None, str(arp), str(stat), str(neigh))
    assert out["ok"] is True
    assert out["entries"] == 1
    assert out["incomplete_count"] == 0
    assert out["verdict"]["verdict"] == "ok"


def test_status_overflow_synthetic(tmp_path):
    arp = tmp_path / "arp"
    arp.write_text(
        ARP_HEADER + _arp_line("1.2.3.4", "aa:bb:cc:dd:ee:ff"))
    stat = tmp_path / "stat"
    stat.write_text(_stat_text(_stat_row(table_fulls=5)))
    neigh = tmp_path / "neigh"
    neigh.mkdir()
    out = mod.status(None, str(arp), str(stat), str(neigh))
    assert out["ok"] is False
    assert out["verdict"]["verdict"] == "arp_table_overflow"
