"""Tests for modules/net_qdisc_default_audit.py R&D #101.2."""
from __future__ import annotations

import pytest

from gpu_dashboard.modules import net_qdisc_default_audit as mod


# --- classify --------------------------------------------------

def test_classify_unknown():
    v = mod.classify(False, None, None)
    assert v["verdict"] == "unknown"


def test_classify_requires_root():
    v = mod.classify(True, None, None)
    assert v["verdict"] == "requires_root"


def test_classify_ok_fq_codel():
    v = mod.classify(True, "fq_codel", 300)
    assert v["verdict"] == "ok"


def test_classify_ok_cake():
    v = mod.classify(True, "cake", 600)
    assert v["verdict"] == "ok"


def test_classify_pfifo_err():
    v = mod.classify(True, "pfifo_fast", 300)
    assert v["verdict"] == "pfifo_fast_default"


def test_classify_budget_low_warn():
    v = mod.classify(True, "fq_codel", 200)
    assert v["verdict"] == "netdev_budget_low"


def test_classify_noqueue_accent():
    v = mod.classify(True, "noqueue", 300)
    assert v["verdict"] == "noqueue_default"


# Priority : pfifo > budget > noqueue
def test_priority_pfifo_over_budget():
    v = mod.classify(True, "pfifo_fast", 100)
    assert v["verdict"] == "pfifo_fast_default"


def test_priority_budget_over_noqueue():
    v = mod.classify(True, "noqueue", 100)
    assert v["verdict"] == "netdev_budget_low"


# --- status integration ----------------------------------------

def test_status_unknown(tmp_path):
    out = mod.status(None, str(tmp_path / "nope"))
    assert out["verdict"]["verdict"] == "unknown"


def test_status_ok_synthetic(tmp_path):
    d = tmp_path / "net_core"
    d.mkdir()
    (d / "default_qdisc").write_text("fq_codel\n")
    (d / "netdev_budget").write_text("300\n")
    (d / "netdev_max_backlog").write_text("1000\n")
    (d / "netdev_budget_usecs").write_text("2000\n")
    out = mod.status(None, str(d))
    assert out["verdict"]["verdict"] == "ok"
    assert out["default_qdisc"] == "fq_codel"


def test_status_pfifo_synthetic(tmp_path):
    d = tmp_path / "net_core"
    d.mkdir()
    (d / "default_qdisc").write_text("pfifo_fast\n")
    (d / "netdev_budget").write_text("300\n")
    out = mod.status(None, str(d))
    assert (out["verdict"]["verdict"]
            == "pfifo_fast_default")
    assert out["ok"] is False
