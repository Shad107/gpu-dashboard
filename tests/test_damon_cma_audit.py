"""Tests for modules/damon_cma_audit.py — R&D #69.3."""
from __future__ import annotations

import os

import pytest

from gpu_dashboard.modules import damon_cma_audit as mod


def _mk_cma(root, name, *, count=1024, used=512, nr_pages=1024,
                  alloc_pages_success=10, alloc_pages_fail=0):
    d = root / name
    d.mkdir(parents=True, exist_ok=True)
    (d / "count").write_text(f"{count}\n")
    (d / "used").write_text(f"{used}\n")
    (d / "nr_pages").write_text(f"{nr_pages}\n")
    (d / "alloc_pages_success").write_text(
        f"{alloc_pages_success}\n")
    (d / "alloc_pages_fail").write_text(f"{alloc_pages_fail}\n")


def _mk_kdamond(root, id_, *, schemes=0, qt_exceeds=0):
    """Builds /admin/kdamonds/<id>/contexts/0/schemes/0..."""
    base = root / "kdamonds" / str(id_)
    base.mkdir(parents=True, exist_ok=True)
    ctx = base / "contexts" / "0"
    ctx.mkdir(parents=True, exist_ok=True)
    schemes_root = ctx / "schemes"
    schemes_root.mkdir(exist_ok=True)
    for s in range(schemes):
        scheme = schemes_root / str(s)
        scheme.mkdir(exist_ok=True)
        stats = scheme / "stats"
        stats.mkdir(exist_ok=True)
        (stats / "qt_exceeds").write_text(f"{qt_exceeds}\n")


# --- list_cma_regions -------------------------------------------

def test_list_cma_missing(tmp_path):
    assert mod.list_cma_regions(str(tmp_path / "nope")) == []


def test_list_cma_two(tmp_path):
    _mk_cma(tmp_path, "linux,cma", alloc_pages_fail=0)
    _mk_cma(tmp_path, "reserved", alloc_pages_fail=3)
    out = mod.list_cma_regions(str(tmp_path))
    assert len(out) == 2
    by_name = {r["name"]: r for r in out}
    assert by_name["reserved"]["alloc_pages_fail"] == 3


# --- list_kdamonds ----------------------------------------------

def test_list_kdamonds_missing(tmp_path):
    assert mod.list_kdamonds(str(tmp_path / "nope")) == []


def test_list_kdamonds_no_schemes(tmp_path):
    _mk_kdamond(tmp_path, 0, schemes=0)
    out = mod.list_kdamonds(str(tmp_path))
    assert len(out) == 1
    assert out[0]["id"] == "0"
    assert out[0]["scheme_count"] == 0
    assert out[0]["quota_breach_total"] == 0


def test_list_kdamonds_with_quota_breach(tmp_path):
    _mk_kdamond(tmp_path, 0, schemes=2, qt_exceeds=5)
    out = mod.list_kdamonds(str(tmp_path))
    assert out[0]["scheme_count"] == 2
    assert out[0]["quota_breach_total"] == 10  # 5 + 5


# --- classify ---------------------------------------------------

def test_classify_unknown():
    v = mod.classify(False, False, [], [])
    assert v["verdict"] == "unknown"


def test_classify_ok_cma_only():
    v = mod.classify(True, False,
                          [{"name": "cma0", "count": 1024,
                              "used": 200, "nr_pages": 1024,
                              "alloc_pages_success": 5,
                              "alloc_pages_fail": 0}],
                          [])
    assert v["verdict"] == "ok"


def test_classify_cma_failing():
    v = mod.classify(True, False,
                          [{"name": "cma0", "count": 1024,
                              "used": 200, "nr_pages": 1024,
                              "alloc_pages_success": 5,
                              "alloc_pages_fail": 3}],
                          [])
    assert v["verdict"] == "cma_alloc_failing"
    assert "cma0" in v["reason"]


def test_classify_damon_quota_breached():
    v = mod.classify(False, True, [],
                          [{"id": "0", "scheme_count": 1,
                              "quota_breach_total": 5}])
    assert v["verdict"] == "damon_scheme_quota_breached"


def test_classify_damon_no_schemes():
    v = mod.classify(False, True, [],
                          [{"id": "0", "scheme_count": 0,
                              "quota_breach_total": 0}])
    assert v["verdict"] == "damon_enabled_no_schemes"


def test_classify_damon_mixed_some_schemes_ok():
    # If at least one kdamond HAS schemes, no alert.
    v = mod.classify(False, True, [],
                          [{"id": "0", "scheme_count": 0,
                              "quota_breach_total": 0},
                            {"id": "1", "scheme_count": 2,
                              "quota_breach_total": 0}])
    assert v["verdict"] == "ok"


# Priority : cma > damon_quota > damon_no_schemes
def test_priority_cma_over_damon_quota():
    v = mod.classify(True, True,
                          [{"name": "cma0", "count": 1024,
                              "used": 200, "nr_pages": 1024,
                              "alloc_pages_success": 5,
                              "alloc_pages_fail": 1}],
                          [{"id": "0", "scheme_count": 1,
                              "quota_breach_total": 5}])
    assert v["verdict"] == "cma_alloc_failing"


def test_priority_damon_quota_over_no_schemes():
    v = mod.classify(False, True, [],
                          [{"id": "0", "scheme_count": 1,
                              "quota_breach_total": 5},
                            {"id": "1", "scheme_count": 0,
                              "quota_breach_total": 0}])
    assert v["verdict"] == "damon_scheme_quota_breached"


# --- status integration -----------------------------------------

def test_status_unknown(tmp_path):
    out = mod.status(None,
                          str(tmp_path / "no_damon"),
                          str(tmp_path / "no_cma"))
    assert out["ok"] is False
    assert out["verdict"]["verdict"] == "unknown"


def test_status_cma_only(tmp_path):
    cma = tmp_path / "cma"; cma.mkdir()
    _mk_cma(cma, "linux,cma")
    out = mod.status(None,
                          str(tmp_path / "no_damon"),
                          str(cma))
    assert out["ok"] is True
    assert out["cma_region_count"] == 1
    assert out["verdict"]["verdict"] == "ok"


def test_status_cma_failing_synthetic(tmp_path):
    cma = tmp_path / "cma"; cma.mkdir()
    _mk_cma(cma, "linux,cma", alloc_pages_fail=42)
    out = mod.status(None,
                          str(tmp_path / "no_damon"),
                          str(cma))
    assert out["verdict"]["verdict"] == "cma_alloc_failing"
