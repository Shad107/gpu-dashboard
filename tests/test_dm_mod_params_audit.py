"""Tests for modules/dm_mod_params_audit.py R&D #108.3."""
from __future__ import annotations

import pytest

from gpu_dashboard.modules import dm_mod_params_audit as mod


def test_classify_unknown():
    v = mod.classify(False, None, None)
    assert v["verdict"] == "unknown"


def test_classify_requires_root():
    v = mod.classify(True, None, None)
    assert v["verdict"] == "requires_root"


def test_classify_ok():
    v = mod.classify(True, True, -1)
    assert v["verdict"] == "ok"


def test_classify_blk_mq_off_accent():
    v = mod.classify(True, False, -1)
    assert v["verdict"] == "dm_use_blk_mq_off"


def test_classify_numa_pinned_accent():
    v = mod.classify(True, True, 0)
    assert v["verdict"] == "dm_numa_node_pinned"


# Priority : blk_mq_off > numa_pinned
def test_priority_blk_mq_over_numa():
    v = mod.classify(True, False, 0)
    assert v["verdict"] == "dm_use_blk_mq_off"


def test_status_unknown(tmp_path):
    out = mod.status(None, str(tmp_path / "nope"))
    assert out["verdict"]["verdict"] == "unknown"


def test_status_ok(tmp_path):
    d = tmp_path / "params"
    d.mkdir()
    (d / "use_blk_mq").write_text("Y\n")
    (d / "dm_numa_node").write_text("-1\n")
    out = mod.status(None, str(d))
    assert out["verdict"]["verdict"] == "ok"


def test_status_blk_mq_off(tmp_path):
    d = tmp_path / "params"
    d.mkdir()
    (d / "use_blk_mq").write_text("N\n")
    (d / "dm_numa_node").write_text("-1\n")
    out = mod.status(None, str(d))
    assert out["verdict"]["verdict"] == "dm_use_blk_mq_off"
