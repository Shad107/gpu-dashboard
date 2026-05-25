"""Tests for modules/bql_stall_counters_audit.py R&D #100.2."""
from __future__ import annotations

import pytest

from gpu_dashboard.modules import bql_stall_counters_audit as mod


def _mk_iface(root, iface, queues):
    """queues: list of (q_name, stall_cnt, stall_max, stall_thrs)"""
    d = root / iface / "queues"
    d.mkdir(parents=True, exist_ok=True)
    for qn, cnt, mx, thrs in queues:
        b = d / qn / "byte_queue_limits"
        b.mkdir(parents=True, exist_ok=True)
        (b / "stall_cnt").write_text(f"{cnt}\n")
        (b / "stall_max").write_text(f"{mx}\n")
        (b / "stall_thrs").write_text(f"{thrs}\n")


# --- is_physical -----------------------------------------------

def test_is_physical():
    assert mod.is_physical("ens18") is True
    assert mod.is_physical("eth0") is True
    assert mod.is_physical("wlan0") is True


def test_is_physical_virtual():
    assert mod.is_physical("lo") is False
    assert mod.is_physical("docker0") is False
    assert mod.is_physical("virbr0") is False
    assert mod.is_physical("br-abc123") is False
    assert mod.is_physical("veth9") is False
    assert mod.is_physical("tun0") is False


# --- walk_all --------------------------------------------------

def test_walk_all_empty(tmp_path):
    assert mod.walk_all(str(tmp_path / "nope")) == []


def test_walk_all_skips_lo(tmp_path):
    _mk_iface(tmp_path, "lo",
                 [("tx-0", 0, 0, 0)])
    _mk_iface(tmp_path, "ens18",
                 [("tx-0", 0, 0, 0), ("tx-1", 0, 0, 0)])
    out = mod.walk_all(str(tmp_path))
    assert len(out) == 1
    assert out[0]["iface"] == "ens18"
    assert len(out[0]["queues"]) == 2


# --- classify --------------------------------------------------

def _e(iface, qs):
    return {"iface": iface,
            "queues": [{"queue": qn, "stall_cnt": c,
                          "stall_max": m, "stall_thrs": t}
                         for qn, c, m, t in qs]}


def test_classify_unknown_no_net():
    v = mod.classify(False, [], False)
    assert v["verdict"] == "unknown"


def test_classify_requires_root():
    v = mod.classify(True, [], False)
    assert v["verdict"] == "requires_root"


def test_classify_no_ifaces_unknown():
    v = mod.classify(True, [], True)
    assert v["verdict"] == "unknown"


def test_classify_ok():
    v = mod.classify(
        True,
        [_e("ens18", [("tx-0", 0, 0, 100)])],
        True)
    assert v["verdict"] == "ok"


def test_classify_stall_max_5s_err():
    v = mod.classify(
        True,
        [_e("ens18", [("tx-0", 5, 6000, 100)])],
        True)
    assert v["verdict"] == "bql_stall_max_above_5s"


def test_classify_stall_cnt_warn():
    v = mod.classify(
        True,
        [_e("ens18", [("tx-0", 3, 200, 100)])],
        True)
    assert v["verdict"] == "bql_stall_cnt_growing"


def test_classify_thrs_zero_accent():
    v = mod.classify(
        True,
        [_e("ens18", [("tx-0", 0, 0, 0)])],
        True)
    assert v["verdict"] == "bql_stall_thrs_disabled"


# Priority : 5s > cnt > thrs
def test_priority_5s_over_cnt():
    v = mod.classify(
        True,
        [_e("ens18", [("tx-0", 10, 6000, 100)])],
        True)
    assert v["verdict"] == "bql_stall_max_above_5s"


def test_priority_cnt_over_thrs():
    v = mod.classify(
        True,
        [_e("ens18", [("tx-0", 3, 200, 0)])],
        True)
    assert v["verdict"] == "bql_stall_cnt_growing"


# --- status integration ----------------------------------------

def test_status_unknown(tmp_path):
    out = mod.status(None, str(tmp_path / "nope"))
    assert out["verdict"]["verdict"] == "unknown"


def test_status_thrs_disabled_synthetic(tmp_path):
    _mk_iface(tmp_path, "ens18",
                 [("tx-0", 0, 0, 0), ("tx-1", 0, 0, 0)])
    out = mod.status(None, str(tmp_path))
    assert (out["verdict"]["verdict"]
            == "bql_stall_thrs_disabled")
    assert out["iface_count"] == 1
    assert out["queue_count"] == 2
