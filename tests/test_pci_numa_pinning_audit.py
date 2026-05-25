"""Tests for modules/pci_numa_pinning_audit.py R&D #109.4."""
from __future__ import annotations

import pytest

from gpu_dashboard.modules import pci_numa_pinning_audit as mod


def _mk_pci(root, bdf, numa_node):
    d = root / "devices" / bdf
    d.mkdir(parents=True, exist_ok=True)
    (d / "numa_node").write_text(f"{numa_node}\n")


def test_classify_unknown():
    v = mod.classify(False, {}, False)
    assert v["verdict"] == "unknown"


def test_classify_requires_root():
    v = mod.classify(True, {}, False)
    assert v["verdict"] == "requires_root"


def test_classify_single_node_is_ok():
    v = mod.classify(True, {"a": -1, "b": -1}, False)
    assert v["verdict"] == "ok"


def test_classify_all_minus_1_multi_warn():
    v = mod.classify(True, {"a": -1, "b": -1, "c": -1},
                          True)
    assert v["verdict"] == "all_devices_node_minus_1_multinode_host"


def test_classify_skew_accent():
    devs = {f"d{i}": 0 for i in range(9)}
    devs["d9"] = 1
    v = mod.classify(True, devs, True)
    assert v["verdict"] == "pci_numa_skew"


def test_classify_balanced_ok():
    devs = {f"d{i}": (i % 2) for i in range(10)}
    v = mod.classify(True, devs, True)
    assert v["verdict"] == "ok"


def test_status_unknown(tmp_path):
    out = mod.status(None,
                       str(tmp_path / "no_pci"),
                       str(tmp_path / "no_node"))
    assert out["verdict"]["verdict"] == "unknown"


def test_status_single_node(tmp_path):
    _mk_pci(tmp_path, "0000:00:00.0", -1)
    _mk_pci(tmp_path, "0000:01:00.0", -1)
    nm = tmp_path / "online"
    nm.write_text("0\n")
    out = mod.status(None, str(tmp_path / "devices"),
                       str(nm))
    assert out["verdict"]["verdict"] == "ok"
    assert out["device_count"] == 2
    assert out["n_unpinned"] == 2


def test_status_multinode_all_minus_1(tmp_path):
    for bdf in ("0000:00:00.0", "0000:01:00.0",
                "0000:02:00.0"):
        _mk_pci(tmp_path, bdf, -1)
    nm = tmp_path / "online"
    nm.write_text("0-1\n")
    out = mod.status(None, str(tmp_path / "devices"),
                       str(nm))
    assert (out["verdict"]["verdict"]
            == "all_devices_node_minus_1_multinode_host")
