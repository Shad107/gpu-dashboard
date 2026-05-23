"""Tests for modules/vm_sysctl_audit.py — R&D #32.4 VM sysctl audit."""
from __future__ import annotations

from pathlib import Path

import pytest

from gpu_dashboard.modules import vm_sysctl_audit


def _mk_sysctls(root: Path, **values):
    root.mkdir(parents=True, exist_ok=True)
    for k, v in values.items():
        (root / k).write_text(str(v) + "\n")


# --- field reader --------------------------------------------------

def test_read_sysctl_int(tmp_path):
    _mk_sysctls(tmp_path, swappiness=60)
    assert vm_sysctl_audit.read_sysctl(str(tmp_path), "swappiness") == 60


def test_read_sysctl_missing_returns_none(tmp_path):
    assert vm_sysctl_audit.read_sysctl(str(tmp_path), "absent") is None


def test_read_sysctl_garbage_returns_none(tmp_path):
    (tmp_path / "weird").write_text("not_a_number\n")
    assert vm_sysctl_audit.read_sysctl(str(tmp_path), "weird") is None


def test_read_sysctl_handles_whitespace(tmp_path):
    (tmp_path / "swappiness").write_text("  60 \n")
    assert vm_sysctl_audit.read_sysctl(str(tmp_path), "swappiness") == 60


# --- per-sysctl classification ------------------------------------

def test_classify_swappiness_default_is_suboptimal():
    v = vm_sysctl_audit.classify_one("swappiness", 60)
    assert v["severity"] == "warn"
    assert "swap" in v["reason"].lower()
    assert v["recommended"] == 10


def test_classify_swappiness_low_is_ok():
    v = vm_sysctl_audit.classify_one("swappiness", 10)
    assert v["severity"] == "ok"


def test_classify_swappiness_zero_is_ok():
    v = vm_sysctl_audit.classify_one("swappiness", 0)
    assert v["severity"] == "ok"


def test_classify_swappiness_extreme_high_is_warn():
    v = vm_sysctl_audit.classify_one("swappiness", 100)
    assert v["severity"] == "warn"


def test_classify_zone_reclaim_zero_is_ok():
    v = vm_sysctl_audit.classify_one("zone_reclaim_mode", 0)
    assert v["severity"] == "ok"


def test_classify_zone_reclaim_nonzero_is_warn():
    v = vm_sysctl_audit.classify_one("zone_reclaim_mode", 1)
    assert v["severity"] == "warn"
    assert "numa" in v["reason"].lower() or "reclaim" in v["reason"].lower()


def test_classify_overcommit_strict_is_warn():
    # mode=2 (strict) + ratio=50 → only 50% of RAM available for malloc
    # which an LLM daemon will blow past instantly
    v = vm_sysctl_audit.classify_one("overcommit_memory", 2)
    assert v["severity"] == "warn"
    assert "strict" in v["reason"].lower() or "overcommit" in v["reason"].lower()


def test_classify_overcommit_heuristic_is_ok():
    v = vm_sysctl_audit.classify_one("overcommit_memory", 0)
    assert v["severity"] == "ok"


def test_classify_overcommit_always_is_ok():
    v = vm_sysctl_audit.classify_one("overcommit_memory", 1)
    assert v["severity"] == "ok"


def test_classify_unknown_key_returns_unknown():
    v = vm_sysctl_audit.classify_one("never_heard_of_it", 42)
    assert v["severity"] == "unknown"


def test_classify_none_value_returns_unknown():
    v = vm_sysctl_audit.classify_one("swappiness", None)
    assert v["severity"] == "unknown"


# --- aggregate ----------------------------------------------------

def test_aggregate_pure_ok():
    rows = [{"name": "swappiness", "value": 10, "severity": "ok"},
            {"name": "zone_reclaim_mode", "value": 0, "severity": "ok"}]
    assert vm_sysctl_audit.aggregate(rows) == "ok"


def test_aggregate_with_one_warn():
    rows = [{"name": "swappiness", "value": 60, "severity": "warn"},
            {"name": "zone_reclaim_mode", "value": 0, "severity": "ok"}]
    assert vm_sysctl_audit.aggregate(rows) == "warn"


def test_aggregate_empty():
    assert vm_sysctl_audit.aggregate([]) == "unknown"


# --- recipe generation -------------------------------------------

def test_recipe_contains_drop_in_path():
    flagged = [{"name": "swappiness", "value": 60, "recommended": 10}]
    r = vm_sysctl_audit.make_recipe(flagged)
    assert "/etc/sysctl.d/" in r
    assert "vm.swappiness=10" in r
    assert "sysctl --system" in r or "sysctl -p" in r


def test_recipe_handles_multiple_flagged():
    flagged = [
        {"name": "swappiness", "value": 60, "recommended": 10},
        {"name": "zone_reclaim_mode", "value": 1, "recommended": 0},
    ]
    r = vm_sysctl_audit.make_recipe(flagged)
    assert "vm.swappiness=10" in r
    assert "vm.zone_reclaim_mode=0" in r


def test_recipe_empty_when_nothing_flagged():
    assert vm_sysctl_audit.make_recipe([]) == ""


# --- status ------------------------------------------------------

def test_status_no_sysctl_dir_returns_missing(tmp_path, monkeypatch):
    monkeypatch.setattr(vm_sysctl_audit, "_SYSCTL_ROOT",
                          str(tmp_path / "absent"))
    s = vm_sysctl_audit.status()
    assert s["ok"] is False
    assert s["error"] == "sysctl_unavailable"


def test_status_default_kernel_flags_swappiness(tmp_path, monkeypatch):
    # The live-rig case: every default kernel ships swappiness=60
    _mk_sysctls(tmp_path,
                swappiness=60,
                vfs_cache_pressure=100,
                overcommit_memory=0,
                overcommit_ratio=50,
                dirty_background_ratio=10,
                dirty_ratio=20,
                zone_reclaim_mode=0,
                min_free_kbytes=67584)
    monkeypatch.setattr(vm_sysctl_audit, "_SYSCTL_ROOT", str(tmp_path))
    s = vm_sysctl_audit.status()
    assert s["ok"] is True
    assert s["worst_severity"] == "warn"
    # swappiness flagged
    rows = {r["name"]: r for r in s["rows"]}
    assert rows["swappiness"]["severity"] == "warn"
    # recipe targets sysctl.d
    assert "vm.swappiness=10" in s["recipe"]


def test_status_tuned_kernel_is_ok(tmp_path, monkeypatch):
    _mk_sysctls(tmp_path,
                swappiness=10,
                vfs_cache_pressure=50,
                overcommit_memory=0,
                overcommit_ratio=50,
                dirty_background_ratio=5,
                dirty_ratio=10,
                zone_reclaim_mode=0,
                min_free_kbytes=131072)
    monkeypatch.setattr(vm_sysctl_audit, "_SYSCTL_ROOT", str(tmp_path))
    s = vm_sysctl_audit.status()
    assert s["worst_severity"] == "ok"
    assert s["recipe"] == ""


def test_status_handles_partial_proc_sys_vm(tmp_path, monkeypatch):
    # Some kernels omit zone_reclaim_mode on non-NUMA systems
    _mk_sysctls(tmp_path, swappiness=10, vfs_cache_pressure=100,
                overcommit_memory=0)
    monkeypatch.setattr(vm_sysctl_audit, "_SYSCTL_ROOT", str(tmp_path))
    s = vm_sysctl_audit.status()
    assert s["ok"] is True
    # Absent zone_reclaim_mode → row not present (no false alarm)
    names = {r["name"] for r in s["rows"]}
    assert "zone_reclaim_mode" not in names


def test_status_extreme_swappiness_flagged_warn(tmp_path, monkeypatch):
    _mk_sysctls(tmp_path, swappiness=100)
    monkeypatch.setattr(vm_sysctl_audit, "_SYSCTL_ROOT", str(tmp_path))
    s = vm_sysctl_audit.status()
    assert s["worst_severity"] == "warn"
    assert "vm.swappiness=10" in s["recipe"]
