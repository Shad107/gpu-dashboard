"""Tests for modules/pci_d3cold_runtime_audit.py R&D #97.4."""
from __future__ import annotations

import os
import pytest

from gpu_dashboard.modules import pci_d3cold_runtime_audit as mod


def _mk_pci_dev(root, addr, *, vendor="0x10de",
                  d3cold_allowed=1, control="auto",
                  runtime_status="active",
                  autosuspend_delay_ms=2000,
                  runtime_suspended_time=0,
                  runtime_active_time=0):
    d = root / "devices" / "pci0000:00" / addr
    d.mkdir(parents=True, exist_ok=True)
    (d / "vendor").write_text(vendor + "\n")
    (d / "d3cold_allowed").write_text(
        str(d3cold_allowed) + "\n")
    p = d / "power"
    p.mkdir(exist_ok=True)
    (p / "control").write_text(control + "\n")
    (p / "runtime_status").write_text(runtime_status + "\n")
    (p / "autosuspend_delay_ms").write_text(
        str(autosuspend_delay_ms) + "\n")
    (p / "runtime_suspended_time").write_text(
        str(runtime_suspended_time) + "\n")
    (p / "runtime_active_time").write_text(
        str(runtime_active_time) + "\n")
    return str(d)


def _mk_nested(root, chain, vendor="0x10de", **gpu_kw):
    """chain: list of addr from leaf to closest-to-host."""
    base = root / "devices" / "pci0000:00"
    base.mkdir(parents=True, exist_ok=True)
    # Build nested directories
    cur = base
    for addr in chain:
        cur = cur / addr
        cur.mkdir(exist_ok=True)
        (cur / "vendor").write_text("0x8086\n")
        (cur / "d3cold_allowed").write_text("1\n")
        p = cur / "power"
        p.mkdir(exist_ok=True)
        (p / "control").write_text("auto\n")
        (p / "runtime_status").write_text("active\n")
        (p / "autosuspend_delay_ms").write_text("100\n")
        (p / "runtime_suspended_time").write_text("0\n")
        (p / "runtime_active_time").write_text("0\n")
    # cur now is the leaf — overwrite as GPU
    (cur / "vendor").write_text(vendor + "\n")
    (cur / "d3cold_allowed").write_text(
        str(gpu_kw.get("d3cold_allowed", 1)) + "\n")
    p = cur / "power"
    (p / "control").write_text(
        gpu_kw.get("control", "auto") + "\n")
    (p / "runtime_status").write_text(
        gpu_kw.get("runtime_status", "active") + "\n")
    (p / "autosuspend_delay_ms").write_text(
        str(gpu_kw.get("autosuspend_delay_ms",
                         2000)) + "\n")
    (p / "runtime_suspended_time").write_text(
        str(gpu_kw.get("runtime_suspended_time",
                         0)) + "\n")
    (p / "runtime_active_time").write_text(
        str(gpu_kw.get("runtime_active_time",
                         0)) + "\n")
    return str(cur)


def _mk_drm(root, card="card0", target=None):
    drm = root / "class" / "drm" / card
    drm.mkdir(parents=True, exist_ok=True)
    if target is not None:
        link = drm / "device"
        os.symlink(target, str(link))
    return str(root / "class" / "drm")


# --- find_gpu_pci_path -----------------------------------------

def test_find_gpu_pci_path_none(tmp_path):
    assert mod.find_gpu_pci_path(
        str(tmp_path / "nope")) is None


def test_find_gpu_pci_path_no_cards(tmp_path):
    drm = tmp_path / "class" / "drm"
    drm.mkdir(parents=True)
    assert mod.find_gpu_pci_path(str(drm)) is None


def test_find_gpu_pci_path_nvidia(tmp_path):
    gpu_path = _mk_pci_dev(tmp_path, "0000:01:00.0",
                              vendor="0x10de")
    drm = _mk_drm(tmp_path, target=gpu_path)
    out = mod.find_gpu_pci_path(drm)
    assert out is not None
    assert out.endswith("0000:01:00.0")


def test_find_gpu_pci_path_skips_non_nvidia(tmp_path):
    # card0 = AMD ; card1 = NVIDIA → find NVIDIA
    amd = _mk_pci_dev(tmp_path, "0000:01:00.0",
                        vendor="0x1002")
    nv = _mk_pci_dev(tmp_path, "0000:02:00.0",
                       vendor="0x10de")
    _mk_drm(tmp_path, "card0", target=amd)
    drm = _mk_drm(tmp_path, "card1", target=nv)
    out = mod.find_gpu_pci_path(drm)
    assert out.endswith("0000:02:00.0")


# --- upstream_chain --------------------------------------------

def test_upstream_chain():
    gpu = "/sys/devices/pci0000:00/0000:00:1c.0/0000:01:00.0"
    chain = mod.upstream_chain(gpu)
    assert chain == [
        "/sys/devices/pci0000:00/0000:00:1c.0"]


def test_upstream_chain_deep():
    gpu = ("/sys/devices/pci0000:00/0000:00:01.0/"
           "0000:01:00.0/0000:02:00.0/0000:03:00.0")
    chain = mod.upstream_chain(gpu)
    assert len(chain) == 3
    assert chain[0].endswith("0000:02:00.0")
    assert chain[-1].endswith("0000:00:01.0")


# --- classify --------------------------------------------------

def _gpu(*, d3cold_allowed=1, control="auto",
         runtime_status="active",
         autosuspend_delay_ms=2000,
         runtime_suspended_time=0,
         runtime_active_time=0):
    return {"path": "/x", "addr": "0000:01:00.0",
            "d3cold_allowed": d3cold_allowed,
            "control": control,
            "runtime_status": runtime_status,
            "autosuspend_delay_ms": autosuspend_delay_ms,
            "runtime_suspended_time":
                runtime_suspended_time,
            "runtime_active_time": runtime_active_time}


def _bridge(*, addr="0000:00:1c.0", d3cold_allowed=1,
            control="auto"):
    return {"addr": addr,
            "d3cold_allowed": d3cold_allowed,
            "control": control,
            "runtime_status": "active",
            "autosuspend_delay_ms": 100,
            "runtime_suspended_time": 0,
            "runtime_active_time": 0,
            "path": "/x"}


def test_classify_unknown():
    v = mod.classify(None, [], False)
    assert v["verdict"] == "unknown"


def test_classify_requires_root():
    v = mod.classify(_gpu(), [], True)
    assert v["verdict"] == "requires_root"


def test_classify_ok():
    v = mod.classify(_gpu(runtime_active_time=10_000,
                          runtime_suspended_time=200_000),
                          [_bridge()], False)
    assert v["verdict"] == "ok"


def test_classify_d3cold_blocked_by_upstream():
    v = mod.classify(
        _gpu(d3cold_allowed=1),
        [_bridge(d3cold_allowed=0)], False)
    assert v["verdict"] == "gpu_d3cold_blocked_by_upstream"


def test_classify_d3cold_blocked_by_control_on():
    v = mod.classify(
        _gpu(d3cold_allowed=1),
        [_bridge(control="on")], False)
    assert v["verdict"] == "gpu_d3cold_blocked_by_upstream"


def test_classify_runtime_pm_disabled():
    v = mod.classify(
        _gpu(control="on"),
        [_bridge()], False)
    assert v["verdict"] == "runtime_pm_disabled_on_gpu"


def test_classify_autosuspend_unset():
    v = mod.classify(
        _gpu(autosuspend_delay_ms=None),
        [_bridge()], False)
    assert v["verdict"] == "autosuspend_delay_unset"


def test_classify_ratio_low():
    v = mod.classify(
        _gpu(runtime_active_time=1_000_000,
             runtime_suspended_time=1000),
        [_bridge()], False)
    assert v["verdict"] == "suspended_active_ratio_low"


def test_classify_ratio_low_ignored_when_idle():
    # Total < 60s → don't fire the ratio verdict
    v = mod.classify(
        _gpu(runtime_active_time=1000,
             runtime_suspended_time=0),
        [_bridge()], False)
    assert v["verdict"] == "ok"


# Priority : blocked > disabled > unset > ratio
def test_priority_blocked_over_disabled():
    v = mod.classify(
        _gpu(d3cold_allowed=1, control="on"),
        [_bridge(d3cold_allowed=0)], False)
    assert v["verdict"] == "gpu_d3cold_blocked_by_upstream"


def test_priority_disabled_over_unset():
    v = mod.classify(
        _gpu(control="on", autosuspend_delay_ms=None),
        [_bridge()], False)
    assert v["verdict"] == "runtime_pm_disabled_on_gpu"


# --- status integration ----------------------------------------

def test_status_unknown_no_drm(tmp_path):
    out = mod.status(None,
                       str(tmp_path / "nodrm"))
    assert out["verdict"]["verdict"] == "unknown"
    assert out["gpu_addr"] is None


def test_status_ok_synthetic(tmp_path):
    gpu = _mk_nested(
        tmp_path, ["0000:00:1c.0", "0000:01:00.0"],
        runtime_active_time=1000,
        runtime_suspended_time=200_000)
    drm = _mk_drm(tmp_path, target=gpu)
    out = mod.status(None, drm)
    assert out["verdict"]["verdict"] == "ok"
    assert out["gpu_addr"] == "0000:01:00.0"
    assert out["upstream_count"] == 1
