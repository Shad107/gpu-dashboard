"""Tests for modules/fuse_connections_audit.py R&D #98.3."""
from __future__ import annotations

import pytest

from gpu_dashboard.modules import fuse_connections_audit as mod


def _mk_conn(root, cid, *, waiting=0, max_bg=12, cong=12):
    d = root / cid
    d.mkdir(parents=True, exist_ok=True)
    (d / "waiting").write_text(str(waiting) + "\n")
    (d / "max_background").write_text(str(max_bg) + "\n")
    (d / "congestion_threshold").write_text(
        str(cong) + "\n")


# --- walk_connections ------------------------------------------

def test_walk_missing(tmp_path):
    assert mod.walk_connections(
        str(tmp_path / "nope")) == []


def test_walk_basic(tmp_path):
    _mk_conn(tmp_path, "42", waiting=3)
    _mk_conn(tmp_path, "43", waiting=0)
    out = mod.walk_connections(str(tmp_path))
    assert len(out) == 2
    by_id = {c["id"]: c for c in out}
    assert by_id["42"]["waiting"] == 3
    assert by_id["43"]["waiting"] == 0


# --- classify --------------------------------------------------

def _c(*, cid="1", waiting=0, max_bg=12, cong=12):
    return {"id": cid, "waiting": waiting,
            "max_background": max_bg,
            "congestion_threshold": cong}


def test_classify_unknown():
    v = mod.classify(False, False, [])
    assert v["verdict"] == "unknown"


def test_classify_requires_root():
    v = mod.classify(True, False, [])
    assert v["verdict"] == "requires_root"


def test_classify_ok():
    v = mod.classify(True, True, [_c(), _c(cid="2")])
    assert v["verdict"] == "ok"


def test_classify_ok_zero_connections():
    v = mod.classify(True, True, [])
    assert v["verdict"] == "ok"


def test_classify_wedged_err():
    v = mod.classify(True, True,
                          [_c(waiting=10)])
    assert v["verdict"] == "fuse_connection_wedged"


def test_classify_count_warn():
    conns = [_c(cid=str(i)) for i in range(55)]
    v = mod.classify(True, True, conns)
    assert v["verdict"] == "fuse_connection_count_high"


def test_classify_congestion_low_accent():
    v = mod.classify(True, True,
                          [_c(cong=2)])
    assert v["verdict"] == "congestion_threshold_low"


def test_classify_kernel_default_cong_is_ok():
    # cong=9 is the kernel default for max_background=12
    v = mod.classify(True, True,
                          [_c(cong=9, max_bg=12)])
    assert v["verdict"] == "ok"


# Priority : wedged > count > congestion
def test_priority_wedged_over_count():
    conns = [_c(cid=str(i)) for i in range(60)]
    conns[0]["waiting"] = 10
    v = mod.classify(True, True, conns)
    assert v["verdict"] == "fuse_connection_wedged"


def test_priority_count_over_congestion():
    conns = [_c(cid=str(i), cong=1) for i in range(55)]
    v = mod.classify(True, True, conns)
    assert v["verdict"] == "fuse_connection_count_high"


# --- status integration ----------------------------------------

def test_status_unknown(tmp_path):
    out = mod.status(None, str(tmp_path / "nope"))
    assert out["verdict"]["verdict"] == "unknown"


def test_status_ok_empty(tmp_path):
    root = tmp_path / "fuse"
    root.mkdir()
    out = mod.status(None, str(root))
    assert out["verdict"]["verdict"] == "ok"
    assert out["connection_count"] == 0


def test_status_wedged(tmp_path):
    root = tmp_path / "fuse"
    root.mkdir()
    _mk_conn(root, "42", waiting=20)
    out = mod.status(None, str(root))
    assert out["verdict"]["verdict"] == "fuse_connection_wedged"
    assert out["max_waiting"] == 20
