"""Tests for modules/cache_l2_imbalance_audit.py R&D #106.3."""
from __future__ import annotations

import pytest

from gpu_dashboard.modules import cache_l2_imbalance_audit as mod


def test_parse_size_kib():
    assert mod.parse_size_to_kib("4096K") == 4096
    assert mod.parse_size_to_kib("2M") == 2048
    assert mod.parse_size_to_kib("1G") == 1024 * 1024
    assert mod.parse_size_to_kib(None) is None
    assert mod.parse_size_to_kib("garbage") is None


def _mk_cpu(root, cpu_id, l2_size):
    d = root / f"cpu{cpu_id}" / "cache" / "index2"
    d.mkdir(parents=True, exist_ok=True)
    (d / "size").write_text(l2_size + "\n")


def test_walk_l2_empty(tmp_path):
    assert mod.walk_l2(str(tmp_path / "nope")) == {}


def test_walk_l2_uniform(tmp_path):
    _mk_cpu(tmp_path, 0, "4096K")
    _mk_cpu(tmp_path, 1, "4096K")
    out = mod.walk_l2(str(tmp_path))
    assert out == {0: 4096, 1: 4096}


def test_classify_unknown():
    v = mod.classify(False, {})
    assert v["verdict"] == "unknown"


def test_classify_ok_no_l2():
    v = mod.classify(True, {})
    assert v["verdict"] == "ok"


def test_classify_uniform():
    v = mod.classify(True, {0: 4096, 1: 4096, 2: 4096})
    assert v["verdict"] == "ok"


def test_classify_imbalance():
    # Alder Lake style: P-cores 1280K, E-cores 2048K
    v = mod.classify(True, {0: 1280, 1: 1280,
                                 4: 2048, 5: 2048})
    assert v["verdict"] == "l2_island_imbalance"


def test_status_unknown(tmp_path):
    out = mod.status(None, str(tmp_path / "nope"))
    assert out["verdict"]["verdict"] == "unknown"


def test_status_uniform(tmp_path):
    for i in range(4):
        _mk_cpu(tmp_path, i, "4096K")
    out = mod.status(None, str(tmp_path))
    assert out["verdict"]["verdict"] == "ok"
    assert out["l2_sizes_kib"] == [4096]


def test_status_imbalance(tmp_path):
    for i in range(2):
        _mk_cpu(tmp_path, i, "1280K")
    for i in range(2, 6):
        _mk_cpu(tmp_path, i, "2048K")
    out = mod.status(None, str(tmp_path))
    assert out["verdict"]["verdict"] == "l2_island_imbalance"
    assert out["l2_sizes_kib"] == [1280, 2048]
