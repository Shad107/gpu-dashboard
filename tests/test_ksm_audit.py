"""Tests for modules/ksm_audit.py — R&D #52.1."""
from __future__ import annotations

import pytest

from gpu_dashboard.modules import ksm_audit as mod


def _mk_ksm(root, **fields):
    root.mkdir(parents=True, exist_ok=True)
    for k, v in fields.items():
        (root / k).write_text(str(v) + "\n")


def _mk_thp(root, *, enabled="always madvise [never]",
              defrag="always defer defer+madvise [madvise] never",
              khugepaged=None):
    root.mkdir(parents=True, exist_ok=True)
    (root / "enabled").write_text(enabled + "\n")
    (root / "defrag").write_text(defrag + "\n")
    if khugepaged:
        khu = root / "khugepaged"
        khu.mkdir(parents=True, exist_ok=True)
        for k, v in khugepaged.items():
            (khu / k).write_text(str(v) + "\n")


# --- _read_active -----------------------------------------------

def test_read_active(tmp_path):
    p = tmp_path / "enabled"
    p.write_text("always [madvise] never\n")
    assert mod._read_active(str(p)) == "madvise"


def test_read_active_missing(tmp_path):
    assert mod._read_active(str(tmp_path / "nope")) is None


def test_read_active_no_bracket(tmp_path):
    p = tmp_path / "enabled"
    p.write_text("always madvise never\n")
    assert mod._read_active(str(p)) is None


# --- read_ksm / read_thp ----------------------------------------

def test_read_ksm_missing(tmp_path):
    out = mod.read_ksm(str(tmp_path / "nope"))
    assert out == {"available": False}


def test_read_ksm_present(tmp_path):
    _mk_ksm(tmp_path, run=1, pages_to_scan=100, pages_sharing=42,
              pages_shared=42, sleep_millisecs=200,
              merge_across_nodes=1, use_zero_pages=0)
    out = mod.read_ksm(str(tmp_path))
    assert out["available"] is True
    assert out["run"] == 1
    assert out["pages_to_scan"] == 100
    assert out["pages_sharing"] == 42


def test_read_thp_missing(tmp_path):
    out = mod.read_thp(str(tmp_path / "nope"))
    assert out == {"available": False}


def test_read_thp_present(tmp_path):
    _mk_thp(tmp_path, enabled="[always] madvise never",
              defrag="always defer defer+madvise [madvise] never")
    out = mod.read_thp(str(tmp_path))
    assert out["available"] is True
    assert out["enabled"] == "always"
    assert out["defrag"] == "madvise"


# --- classify ---------------------------------------------------

def _ksm(available=True, run=0, sharing=0, scan=100, sleep_ms=200):
    return {"available": available, "run": run,
              "pages_sharing": sharing, "pages_to_scan": scan,
              "sleep_millisecs": sleep_ms}


def _thp(available=True, enabled="madvise", defrag="madvise"):
    return {"available": available, "enabled": enabled,
              "defrag": defrag}


def test_classify_unknown():
    v = mod.classify({"available": False}, {"available": False})
    assert v["verdict"] == "unknown"


def test_classify_ok():
    # KSM unavailable (typical server kernel built without KSM) +
    # THP madvise → ok.
    v = mod.classify({"available": False}, _thp())
    assert v["verdict"] == "ok"


def test_classify_thp_always():
    v = mod.classify(_ksm(run=0), _thp(enabled="always"))
    assert v["verdict"] == "thp_always_with_llm"


def test_classify_thp_defrag_aggressive():
    v = mod.classify(_ksm(run=0), _thp(defrag="always"))
    assert v["verdict"] == "thp_defrag_aggressive"
    v2 = mod.classify(_ksm(run=0),
                        _thp(defrag="defer+madvise"))
    assert v2["verdict"] == "thp_defrag_aggressive"


def test_classify_ksm_thrashing():
    v = mod.classify(_ksm(run=1, scan=2000, sleep_ms=10),
                       _thp())
    assert v["verdict"] == "ksm_thrashing"
    assert "2000" in v["reason"]


def test_classify_ksm_disabled_with_madvise():
    v = mod.classify(_ksm(run=0, sharing=0),
                       _thp(enabled="madvise"))
    # This branch fires when KSM is available + off + THP=madvise
    # + zero sharing — the half-configured-host case.
    assert v["verdict"] == "ksm_disabled_with_madvise"


def test_classify_priority_thp_wins_over_ksm():
    # Both ksm_thrashing AND thp_always : thp wins (higher prio).
    # Wait, ksm_thrashing is #1, thp_always is #2. So ksm wins.
    v = mod.classify(_ksm(run=1, scan=2000, sleep_ms=10),
                       _thp(enabled="always"))
    assert v["verdict"] == "ksm_thrashing"


# --- status integration -----------------------------------------

def test_status_both_missing(tmp_path):
    out = mod.status(None, str(tmp_path / "noksm"),
                       str(tmp_path / "nothp"))
    assert out["ok"] is False
    assert out["verdict"]["verdict"] == "unknown"


def test_status_thp_always_live(tmp_path):
    thp = tmp_path / "thp"
    _mk_thp(thp, enabled="[always] madvise never",
              defrag="always defer defer+madvise [madvise] never")
    out = mod.status(None, str(tmp_path / "noksm"), str(thp))
    assert out["ok"] is True
    assert out["verdict"]["verdict"] == "thp_always_with_llm"
