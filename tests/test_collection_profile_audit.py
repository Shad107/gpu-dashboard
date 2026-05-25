"""Tests for modules/collection_profile_audit.py — Hardening #2."""
from __future__ import annotations

import time
import types

import pytest

from gpu_dashboard.modules import collection_profile_audit as mod


# --- time_status ----------------------------------------------------

def test_time_status_returns_ms_for_fast_callable():
    elapsed = mod.time_status(lambda: None)
    assert isinstance(elapsed, float)
    assert 0.0 <= elapsed < 100.0


def test_time_status_measures_sleep():
    elapsed = mod.time_status(lambda: time.sleep(0.05))
    assert elapsed >= 45.0  # 50 ms minus jitter


# --- profile_modules with a synthetic package ---------------------

def _mk_synthetic_pkg(fast=True, slow=True, broken=False,
                          no_status=False, sig_mismatch=False):
    """Build a fake `modules` package with a controllable mix of
    sub-modules."""
    pkg = types.ModuleType("synth_pkg")
    pkg.__path__ = []  # marker for pkgutil
    submods = {}
    if fast:
        m = types.ModuleType("synth_pkg.fast")
        m.status = lambda cfg=None: {"verdict": "ok"}
        submods["fast"] = m
    if slow:
        m = types.ModuleType("synth_pkg.slow")
        def _slow(cfg=None):
            time.sleep(0.6)
            return {"verdict": "ok"}
        m.status = _slow
        submods["slow"] = m
    if broken:
        m = types.ModuleType("synth_pkg.broken")
        def _crash(cfg=None):
            raise RuntimeError("synthetic")
        m.status = _crash
        submods["broken"] = m
    if no_status:
        m = types.ModuleType("synth_pkg.no_status")
        submods["no_status"] = m
    if sig_mismatch:
        m = types.ModuleType("synth_pkg.sigmm")
        m.status = lambda cfg, root: {"verdict": "ok"}
        submods["sigmm"] = m
    return pkg, submods


def _install_synth(monkeypatch, pkg, submods):
    """Wire the synthetic package so the module-under-test's calls
    to ``pkgutil.iter_modules`` and ``importlib.import_module`` see
    the synthetic sub-modules."""
    def fake_iter(paths):
        for n in submods:
            yield types.SimpleNamespace(name=n, ispkg=False)
    monkeypatch.setattr(mod.pkgutil, "iter_modules", fake_iter)
    def fake_import(name):
        return submods[name.split(".")[-1]]
    monkeypatch.setattr(mod.importlib, "import_module", fake_import)


def test_profile_modules_records_elapsed_for_fast(monkeypatch):
    pkg, submods = _mk_synthetic_pkg(fast=True, slow=False)
    _install_synth(monkeypatch, pkg, submods)
    results = mod.profile_modules(pkg=pkg)
    assert len(results) == 1
    assert results[0]["name"] == "fast"
    assert results[0]["status"] == "ok"
    assert results[0]["elapsed_ms"] is not None
    assert results[0]["elapsed_ms"] < 100.0


def test_profile_modules_records_slow(monkeypatch):
    pkg, submods = _mk_synthetic_pkg(fast=False, slow=True)
    _install_synth(monkeypatch, pkg, submods)
    results = mod.profile_modules(pkg=pkg)
    r = next(r for r in results if r["name"] == "slow")
    assert r["elapsed_ms"] >= 500.0


def test_profile_modules_marks_broken(monkeypatch):
    pkg, submods = _mk_synthetic_pkg(fast=False, slow=False,
                                          broken=True)
    _install_synth(monkeypatch, pkg, submods)
    results = mod.profile_modules(pkg=pkg)
    r = next(r for r in results if r["name"] == "broken")
    assert r["status"] == "error:RuntimeError"
    assert r["elapsed_ms"] is None


def test_profile_modules_marks_no_status(monkeypatch):
    pkg, submods = _mk_synthetic_pkg(fast=False, slow=False,
                                          no_status=True)
    _install_synth(monkeypatch, pkg, submods)
    results = mod.profile_modules(pkg=pkg)
    r = next(r for r in results if r["name"] == "no_status")
    assert r["status"] == "no-status"


def test_profile_modules_marks_sig_mismatch(monkeypatch):
    pkg, submods = _mk_synthetic_pkg(fast=False, slow=False,
                                          sig_mismatch=True)
    _install_synth(monkeypatch, pkg, submods)
    results = mod.profile_modules(pkg=pkg)
    r = next(r for r in results if r["name"] == "sigmm")
    assert r["status"] == "skipped"


# --- aggregate -----------------------------------------------------

def test_aggregate_empty():
    a = mod.aggregate([])
    assert a["module_count"] == 0
    assert a["p50_ms"] is None


def test_aggregate_basic_stats():
    results = [
        {"name": "a", "elapsed_ms": 10.0, "status": "ok"},
        {"name": "b", "elapsed_ms": 20.0, "status": "ok"},
        {"name": "c", "elapsed_ms": 30.0, "status": "ok"},
        {"name": "d", "elapsed_ms": None, "status": "skipped"}]
    a = mod.aggregate(results)
    assert a["module_count"] == 3
    assert a["total_ms"] == 60.0
    assert a["slowest_ms"] == 30.0


# --- classify -----------------------------------------------------

def test_classify_ok():
    results = [{"name": "x", "elapsed_ms": 10.0, "status": "ok"}]
    v = mod.classify(results, mod.aggregate(results))
    assert v["verdict"] == "ok"


def test_classify_unknown_on_empty():
    v = mod.classify([], mod.aggregate([]))
    assert v["verdict"] == "unknown"


def test_classify_module_too_slow():
    results = [
        {"name": "fast", "elapsed_ms": 50.0, "status": "ok"},
        {"name": "hot",  "elapsed_ms": 800.0, "status": "ok"}]
    v = mod.classify(results, mod.aggregate(results))
    assert v["verdict"] == "module_too_slow"
    assert "hot=800" in v["reason"]


def test_classify_collection_slow_no_individual_offender():
    results = [{"name": f"m{i}", "elapsed_ms": 200.0,
                 "status": "ok"} for i in range(30)]
    v = mod.classify(results, mod.aggregate(results))
    assert v["verdict"] == "collection_slow"


def test_classify_module_too_slow_beats_collection_slow():
    results = ([{"name": "hot", "elapsed_ms": 900.0,
                  "status": "ok"}] +
                [{"name": f"m{i}", "elapsed_ms": 200.0,
                  "status": "ok"} for i in range(30)])
    v = mod.classify(results, mod.aggregate(results))
    assert v["verdict"] == "module_too_slow"


# --- status integration ------------------------------------------

def test_status_live_smoke():
    """Actually walks the real modules package — slow but
    proves end-to-end works."""
    out = mod.status()
    assert out["ok"] is True
    assert out["module_count"] > 100
    assert out["verdict"]["verdict"] in (
        "ok", "module_too_slow", "collection_slow", "unknown")
    assert isinstance(out["top_slowest"], list)
    assert len(out["top_slowest"]) <= 10
