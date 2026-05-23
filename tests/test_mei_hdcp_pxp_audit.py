"""Tests for modules/mei_hdcp_pxp_audit.py — R&D #64.2."""
from __future__ import annotations

import pytest

from gpu_dashboard.modules import mei_hdcp_pxp_audit as mod


def _mk_client(root, name, state="enabled", fw_ver="0:1.0"):
    d = root / name
    d.mkdir(parents=True, exist_ok=True)
    (d / "state").write_text(state + "\n")
    (d / "fw_status").write_text("ok\n")
    (d / "fw_ver").write_text(fw_ver + "\n")
    (d / "hbm_ver").write_text("2.3\n")


def _mk_pci(root, bdf, vendor, klass):
    d = root / bdf
    d.mkdir(parents=True, exist_ok=True)
    (d / "vendor").write_text(vendor + "\n")
    (d / "class").write_text(klass + "\n")


# --- list_subclass ----------------------------------------------

def test_list_subclass_missing(tmp_path):
    assert mod.list_subclass(str(tmp_path / "nope")) == []


def test_list_subclass(tmp_path):
    _mk_client(tmp_path, "i915-0000:03:00.0")
    out = mod.list_subclass(str(tmp_path))
    assert len(out) == 1
    assert out[0]["state"] == "enabled"


# --- has_intel_discrete_gpu -------------------------------------

def test_has_intel_discrete_gpu(tmp_path):
    _mk_pci(tmp_path, "0000:03:00.0", "0x8086", "0x030000")
    out = mod.has_intel_discrete_gpu(str(tmp_path))
    assert out == ["0000:03:00.0"]


def test_has_intel_discrete_gpu_nvidia(tmp_path):
    _mk_pci(tmp_path, "0000:01:00.0", "0x10de", "0x030000")
    assert mod.has_intel_discrete_gpu(str(tmp_path)) == []


# --- classify ---------------------------------------------------

def _hdcp(state="enabled", fw_ver="0:1.0"):
    return {"id": "i915-0000:03:00.0", "state": state,
              "fw_status": "ok", "fw_ver": fw_ver,
              "hbm_ver": "2.3"}


def _pxp(state="enabled"):
    return {"id": "i915-pxp-0000:03:00.0", "state": state,
              "fw_status": "ok", "fw_ver": "0:1.0",
              "hbm_ver": "2.3"}


def test_classify_unknown():
    v = mod.classify([], [], [])
    assert v["verdict"] == "unknown"


def test_classify_ok():
    v = mod.classify([_hdcp()], [_pxp()], ["0000:03:00.0"])
    assert v["verdict"] == "ok"


def test_classify_pxp_disabled_with_gpu():
    v = mod.classify([_hdcp()], [_pxp(state="disabled")],
                       ["0000:03:00.0"])
    assert v["verdict"] == "pxp_disabled_with_gpu"


def test_classify_pxp_disabled_no_gpu():
    # No Intel GPU + HDCP enabled → ok (pxp disabled is fine
    # when no Intel GPU consumer exists).
    v = mod.classify([_hdcp()], [_pxp(state="disabled")], [])
    assert v["verdict"] == "ok"


def test_classify_hdcp_mismatch():
    v = mod.classify([_hdcp(state="disabled")], [], [])
    assert v["verdict"] == "hdcp_fw_mismatch"


def test_classify_no_consumer():
    v = mod.classify([_hdcp(state="disconnected", fw_ver=None)],
                       [_pxp(state="disconnected")], [])
    assert v["verdict"] == "subclasses_no_consumer"


def test_classify_priority_pxp_wins():
    v = mod.classify(
        [_hdcp(state="disabled")],
        [_pxp(state="disabled")],
        ["0000:03:00.0"])
    assert v["verdict"] == "pxp_disabled_with_gpu"


# --- status integration -----------------------------------------

def test_status_unknown(tmp_path):
    out = mod.status(None,
                       str(tmp_path / "nohdcp"),
                       str(tmp_path / "nopxp"),
                       str(tmp_path / "nopci"))
    assert out["ok"] is False
    assert out["verdict"]["verdict"] == "unknown"


def test_status_live_like(tmp_path):
    hdcp = tmp_path / "hdcp"
    pxp = tmp_path / "pxp"
    pci = tmp_path / "pci"
    _mk_client(hdcp, "i915-0000:03:00.0")
    _mk_client(pxp, "i915-pxp-0000:03:00.0")
    _mk_pci(pci, "0000:03:00.0", "0x8086", "0x030000")
    out = mod.status(None, str(hdcp), str(pxp), str(pci))
    assert out["ok"] is True
    assert out["hdcp_count"] == 1
    assert out["pxp_count"] == 1
    assert out["verdict"]["verdict"] == "ok"
