"""Tests for modules/vm_compaction_proactive_audit.py R&D #105.4."""
from __future__ import annotations

import pytest

from gpu_dashboard.modules import vm_compaction_proactive_audit as mod


# --- parse_thp_enabled -----------------------------------------

def test_parse_thp_always():
    assert mod.parse_thp_enabled(
        "[always] madvise never") == "always"


def test_parse_thp_madvise():
    assert mod.parse_thp_enabled(
        "always [madvise] never") == "madvise"


def test_parse_thp_empty():
    assert mod.parse_thp_enabled("") is None
    assert mod.parse_thp_enabled(None) is None


# --- classify --------------------------------------------------

def test_classify_unknown():
    v = mod.classify(False, None, None, None, None)
    assert v["verdict"] == "unknown"


def test_classify_requires_root():
    v = mod.classify(True, None, None, None, None)
    assert v["verdict"] == "requires_root"


def test_classify_ok():
    v = mod.classify(True, 20, 1, 0, "madvise")
    assert v["verdict"] == "ok"


def test_classify_proactive_off_thp_always_warn():
    v = mod.classify(True, 0, 1, 0, "always")
    assert v["verdict"] == "proactive_off_thp_always"


def test_classify_proactive_off_thp_madvise_ok():
    # proactiveness=0 with THP=madvise → not warn, that's fine
    v = mod.classify(True, 0, 1, 0, "madvise")
    assert v["verdict"] == "ok"


def test_classify_aggressive_accent():
    v = mod.classify(True, 80, 1, 0, "madvise")
    assert v["verdict"] == "proactive_aggressive_jank"


def test_classify_pagelist_extreme_accent():
    v = mod.classify(True, 20, 1, 4, "madvise")
    assert v["verdict"] == "pagelist_fraction_extreme"


def test_classify_pagelist_zero_default_ok():
    # 0 = kernel auto-sizes → ok, not flagged
    v = mod.classify(True, 20, 1, 0, "madvise")
    assert v["verdict"] == "ok"


def test_classify_compact_unevictable_off_accent():
    v = mod.classify(True, 20, 0, 0, "madvise")
    assert v["verdict"] == "compact_unevictable_disabled"


# Priority : off_thp > aggressive > pagelist > unevictable
def test_priority_off_thp_over_aggressive():
    # Can't trigger both since one needs =0 and other >=50;
    # verify off-thp wins on edge case (proactiveness=0
    # alone, THP=always)
    v = mod.classify(True, 0, 0, 4, "always")
    assert v["verdict"] == "proactive_off_thp_always"


def test_priority_aggressive_over_pagelist():
    v = mod.classify(True, 60, 1, 4, "madvise")
    assert v["verdict"] == "proactive_aggressive_jank"


# --- status integration ----------------------------------------

def test_status_unknown(tmp_path):
    out = mod.status(None,
                       str(tmp_path / "no_vm"),
                       str(tmp_path / "no_thp"))
    assert out["verdict"]["verdict"] == "unknown"


def test_status_ok_synthetic(tmp_path):
    v = tmp_path / "vm"
    v.mkdir()
    (v / "compaction_proactiveness").write_text("20\n")
    (v / "compact_unevictable_allowed").write_text("1\n")
    (v / "percpu_pagelist_high_fraction").write_text("0\n")
    thp = tmp_path / "thp"
    thp.write_text("always [madvise] never\n")
    out = mod.status(None, str(v), str(thp))
    assert out["verdict"]["verdict"] == "ok"


def test_status_off_thp_always(tmp_path):
    v = tmp_path / "vm"
    v.mkdir()
    (v / "compaction_proactiveness").write_text("0\n")
    (v / "compact_unevictable_allowed").write_text("1\n")
    (v / "percpu_pagelist_high_fraction").write_text("0\n")
    thp = tmp_path / "thp"
    thp.write_text("[always] madvise never\n")
    out = mod.status(None, str(v), str(thp))
    assert (out["verdict"]["verdict"]
            == "proactive_off_thp_always")
