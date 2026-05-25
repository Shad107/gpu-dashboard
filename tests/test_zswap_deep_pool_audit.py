"""Tests for modules/zswap_deep_pool_audit.py R&D #100.4."""
from __future__ import annotations

import pytest

from gpu_dashboard.modules import zswap_deep_pool_audit as mod


# --- classify --------------------------------------------------

def test_classify_unknown():
    v = mod.classify(False, None, None, None,
                          None, None, False)
    assert v["verdict"] == "unknown"


def test_classify_ok_disabled():
    v = mod.classify(True, "N", None, None,
                          None, None, False)
    assert v["verdict"] == "ok"


def test_classify_requires_root():
    # zswap on, params absent, debugfs unreadable
    v = mod.classify(True, "Y", None, None,
                          None, None, True)
    assert v["verdict"] == "requires_root"


def test_classify_ok_all_sane():
    v = mod.classify(True, "Y", "Y", "Y",
                          0, 0, False)
    assert v["verdict"] == "ok"


def test_classify_pool_limit_hit_err():
    v = mod.classify(True, "Y", "Y", "Y",
                          5000, 1200, False)
    assert v["verdict"] == "zswap_pool_limit_hit_persistent"


def test_classify_pool_hit_alone_not_err():
    # pool_limit_hit alone (no reject_compress_poor) → not err
    v = mod.classify(True, "Y", "Y", "Y",
                          5000, 0, False)
    assert v["verdict"] != "zswap_pool_limit_hit_persistent"


def test_classify_exclusive_off_warn():
    v = mod.classify(True, "Y", "N", "Y",
                          0, 0, False)
    assert v["verdict"] == "zswap_exclusive_loads_off"


def test_classify_shrinker_off_accent():
    v = mod.classify(True, "Y", "Y", "N",
                          0, 0, False)
    assert v["verdict"] == "zswap_shrinker_disabled"


# Priority : pool_hit > exclusive > shrinker
def test_priority_pool_over_exclusive():
    v = mod.classify(True, "Y", "N", "N",
                          5000, 1200, False)
    assert v["verdict"] == "zswap_pool_limit_hit_persistent"


def test_priority_exclusive_over_shrinker():
    v = mod.classify(True, "Y", "N", "N",
                          0, 0, False)
    assert v["verdict"] == "zswap_exclusive_loads_off"


# --- status integration ----------------------------------------

def test_status_unknown(tmp_path):
    out = mod.status(None,
                       str(tmp_path / "no_params"),
                       str(tmp_path / "no_debugfs"))
    assert out["verdict"]["verdict"] == "unknown"


def test_status_disabled_synthetic(tmp_path):
    d = tmp_path / "params"
    d.mkdir()
    (d / "enabled").write_text("N\n")
    out = mod.status(None, str(d),
                       str(tmp_path / "no_debugfs"))
    assert out["verdict"]["verdict"] == "ok"
    assert out["enabled"] == "N"


def test_status_exclusive_off_synthetic(tmp_path):
    d = tmp_path / "params"
    d.mkdir()
    (d / "enabled").write_text("Y\n")
    (d / "exclusive_loads").write_text("N\n")
    (d / "shrinker_enabled").write_text("Y\n")
    out = mod.status(None, str(d),
                       str(tmp_path / "no_debugfs"))
    assert (out["verdict"]["verdict"]
            == "zswap_exclusive_loads_off")
