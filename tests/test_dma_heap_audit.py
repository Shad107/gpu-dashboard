"""Tests for modules/dma_heap_audit.py — R&D #74.2."""
from __future__ import annotations

import os

import pytest

from gpu_dashboard.modules import dma_heap_audit as mod


def _mk_heap(sys_dir, dev_dir, name, *, mode=0o600):
    sd = sys_dir / name
    sd.mkdir(parents=True, exist_ok=True)
    dev_dir.mkdir(parents=True, exist_ok=True)
    devnode = dev_dir / name
    devnode.write_text("")
    os.chmod(str(devnode), mode)


# --- list_heaps -------------------------------------------------

def test_list_missing(tmp_path):
    assert mod.list_heaps(str(tmp_path / "nope"),
                                  str(tmp_path / "dev")) == []


def test_list_with_dev_node(tmp_path):
    sd = tmp_path / "sys"
    dv = tmp_path / "dev"
    _mk_heap(sd, dv, "system", mode=0o600)
    _mk_heap(sd, dv, "cma", mode=0o660)
    out = mod.list_heaps(str(sd), str(dv))
    by_name = {h["name"]: h for h in out}
    assert by_name["system"]["dev_node_mode"] == 0o600
    assert by_name["cma"]["dev_node_mode"] == 0o660


# --- detect_gpu_presence ---------------------------------------

def test_detect_gpu_absent(tmp_path):
    assert mod.detect_gpu_presence(str(tmp_path / "nope")) is False


def test_detect_gpu_present(tmp_path):
    (tmp_path / "card0").mkdir()
    (tmp_path / "renderD128").mkdir()
    assert mod.detect_gpu_presence(str(tmp_path)) is True


# --- classify ---------------------------------------------------

def test_classify_unknown():
    v = mod.classify(False, [], False)
    assert v["verdict"] == "unknown"


def test_classify_ok():
    v = mod.classify(True,
                          [{"name": "system",
                              "dev_node_present": True,
                              "dev_node_mode": 0o660},
                            {"name": "cma",
                              "dev_node_present": True,
                              "dev_node_mode": 0o660}],
                          True)
    assert v["verdict"] == "ok"


def test_classify_world_writable():
    v = mod.classify(True,
                          [{"name": "system",
                              "dev_node_present": True,
                              "dev_node_mode": 0o666}],
                          True)
    assert v["verdict"] == "heaps_world_writable"


def test_classify_cma_missing_for_gpu():
    v = mod.classify(True,
                          [{"name": "system",
                              "dev_node_present": True,
                              "dev_node_mode": 0o660}],
                          True)
    assert v["verdict"] == "cma_heap_missing_for_dma_buf"


def test_classify_only_system_no_gpu():
    v = mod.classify(True,
                          [{"name": "system",
                              "dev_node_present": True,
                              "dev_node_mode": 0o660}],
                          False)
    assert v["verdict"] == "only_system_heap_present"


def test_classify_heap_perms_root_only_no_gpu():
    # Multi-heap, all 0600, no GPU → root-only verdict.
    v = mod.classify(True,
                          [{"name": "system",
                              "dev_node_present": True,
                              "dev_node_mode": 0o600},
                            {"name": "cma",
                              "dev_node_present": True,
                              "dev_node_mode": 0o600}],
                          False)
    assert v["verdict"] == "heap_perms_root_only"


# Priority : ww > cma_missing > only_system > perms
def test_priority_ww_over_cma_missing():
    v = mod.classify(True,
                          [{"name": "system",
                              "dev_node_present": True,
                              "dev_node_mode": 0o666}],
                          True)
    assert v["verdict"] == "heaps_world_writable"


def test_priority_cma_missing_over_only_system():
    # GPU + just system → cma_missing wins
    v = mod.classify(True,
                          [{"name": "system",
                              "dev_node_present": True,
                              "dev_node_mode": 0o660}],
                          True)
    assert v["verdict"] == "cma_heap_missing_for_dma_buf"


# --- status integration -----------------------------------------

def test_status_unknown(tmp_path):
    out = mod.status(None,
                          str(tmp_path / "no_sys"),
                          str(tmp_path / "no_dev"),
                          str(tmp_path / "no_drm"))
    assert out["ok"] is False
    assert out["verdict"]["verdict"] == "unknown"


def test_status_only_system_no_gpu(tmp_path):
    sd = tmp_path / "sys"; dv = tmp_path / "dev"
    _mk_heap(sd, dv, "system", mode=0o660)
    out = mod.status(None, str(sd), str(dv),
                          str(tmp_path / "no_drm"))
    assert out["ok"] is True
    assert out["heap_count"] == 1
    assert out["verdict"]["verdict"] == "only_system_heap_present"


def test_status_cma_missing_with_gpu(tmp_path):
    sd = tmp_path / "sys"; dv = tmp_path / "dev"
    _mk_heap(sd, dv, "system", mode=0o660)
    drm = tmp_path / "drm"; drm.mkdir()
    (drm / "card0").mkdir()
    out = mod.status(None, str(sd), str(dv), str(drm))
    assert out["gpu_present"] is True
    assert out["verdict"]["verdict"] == \
        "cma_heap_missing_for_dma_buf"
