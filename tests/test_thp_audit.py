"""Tests for modules/thp_audit.py — R&D #34.1 transparent_hugepage audit."""
from __future__ import annotations

from pathlib import Path

import pytest

from gpu_dashboard.modules import thp_audit


def _mk_thp(root: Path, *, enabled: str = "always [madvise] never",
              defrag: str = "always defer defer+madvise [madvise] never",
              use_zero_page: str = "1",
              khugepaged_defrag: str = "1",
              scan_sleep_ms: str = "10000"):
    root.mkdir(parents=True, exist_ok=True)
    (root / "enabled").write_text(enabled + "\n")
    (root / "defrag").write_text(defrag + "\n")
    (root / "use_zero_page").write_text(use_zero_page + "\n")
    kh = root / "khugepaged"
    kh.mkdir()
    (kh / "defrag").write_text(khugepaged_defrag + "\n")
    (kh / "scan_sleep_millisecs").write_text(scan_sleep_ms + "\n")


# --- parse_bracketed ----------------------------------------------

def test_parse_bracketed_picks_active():
    assert thp_audit.parse_bracketed("always [madvise] never") == "madvise"
    assert thp_audit.parse_bracketed("[always] madvise never") == "always"
    assert thp_audit.parse_bracketed("always madvise [never]") == "never"


def test_parse_bracketed_empty_returns_none():
    assert thp_audit.parse_bracketed("") is None
    assert thp_audit.parse_bracketed(None) is None


def test_parse_bracketed_no_brackets_returns_none():
    assert thp_audit.parse_bracketed("always madvise never") is None


# --- field readers ------------------------------------------------

def test_read_enabled(tmp_path):
    _mk_thp(tmp_path, enabled="[always] madvise never")
    assert thp_audit.read_enabled(str(tmp_path)) == "always"


def test_read_defrag(tmp_path):
    _mk_thp(tmp_path, defrag="[always] defer madvise never")
    assert thp_audit.read_defrag(str(tmp_path)) == "always"


def test_read_missing_returns_none(tmp_path):
    assert thp_audit.read_enabled(str(tmp_path / "absent")) is None


# --- classify ----------------------------------------------------

def test_classify_optimal_always_with_safe_defrag():
    v = thp_audit.classify(enabled="always", defrag="defer+madvise")
    assert v["verdict"] == "optimal"


def test_classify_optimal_always_with_madvise_defrag():
    v = thp_audit.classify(enabled="always", defrag="madvise")
    assert v["verdict"] == "optimal"


def test_classify_disabled_is_warn():
    v = thp_audit.classify(enabled="never", defrag="never")
    assert v["verdict"] == "disabled"
    assert "tlb" in v["reason"].lower() or "hugepage" in v["reason"].lower()


def test_classify_madvise_default_acceptable():
    # The Ubuntu/Debian default — acceptable but elevatable to always
    v = thp_audit.classify(enabled="madvise", defrag="madvise")
    assert v["verdict"] == "madvise_default"


def test_classify_aggressive_defrag_warns():
    # defrag=always can cause sync compaction stalls
    v = thp_audit.classify(enabled="always", defrag="always")
    assert v["verdict"] == "aggressive_defrag"
    assert "defer" in v["recommendation"].lower() or "stall" in v["reason"].lower()


def test_classify_unknown_when_none():
    v = thp_audit.classify(enabled=None, defrag=None)
    assert v["verdict"] == "unknown"


def test_classify_recommendation_includes_runtime_sysfs():
    v = thp_audit.classify(enabled="madvise", defrag="madvise")
    assert "/sys/kernel/mm/transparent_hugepage" in v["recommendation"]


def test_classify_recommendation_includes_persistent_grub():
    v = thp_audit.classify(enabled="never", defrag="never")
    assert "transparent_hugepage=" in v["recommendation"]


# --- status -----------------------------------------------------

def test_status_no_thp_dir(tmp_path, monkeypatch):
    monkeypatch.setattr(thp_audit, "_THP_ROOT", str(tmp_path / "absent"))
    s = thp_audit.status()
    assert s["ok"] is False
    assert s["error"] == "thp_unavailable"


def test_status_full_payload_live_default(tmp_path, monkeypatch):
    # The live-rig case: Ubuntu/Debian default
    _mk_thp(tmp_path,
            enabled="always [madvise] never",
            defrag="always defer defer+madvise [madvise] never")
    monkeypatch.setattr(thp_audit, "_THP_ROOT", str(tmp_path))
    s = thp_audit.status()
    assert s["ok"] is True
    assert s["enabled"] == "madvise"
    assert s["defrag"] == "madvise"
    assert s["verdict"]["verdict"] == "madvise_default"


def test_status_disabled(tmp_path, monkeypatch):
    _mk_thp(tmp_path,
            enabled="always madvise [never]",
            defrag="always defer defer+madvise madvise [never]")
    monkeypatch.setattr(thp_audit, "_THP_ROOT", str(tmp_path))
    s = thp_audit.status()
    assert s["verdict"]["verdict"] == "disabled"


def test_status_includes_khugepaged_scan(tmp_path, monkeypatch):
    _mk_thp(tmp_path, scan_sleep_ms="5000")
    monkeypatch.setattr(thp_audit, "_THP_ROOT", str(tmp_path))
    s = thp_audit.status()
    assert s["khugepaged_scan_sleep_ms"] == 5000


def test_status_optimal_when_always(tmp_path, monkeypatch):
    _mk_thp(tmp_path,
            enabled="[always] madvise never",
            defrag="always defer [defer+madvise] madvise never")
    monkeypatch.setattr(thp_audit, "_THP_ROOT", str(tmp_path))
    s = thp_audit.status()
    assert s["verdict"]["verdict"] == "optimal"
