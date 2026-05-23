"""Tests for modules/kvm_misc_audit.py — R&D #54.3."""
from __future__ import annotations

import os
import stat
import pytest

from gpu_dashboard.modules import kvm_misc_audit as mod


def _mk_kvm(root, *, variant="kvm_intel", nested="N",
              halt_poll_ns=200000):
    kvm = root / "kvm" / "parameters"
    kvm.mkdir(parents=True, exist_ok=True)
    (kvm / "halt_poll_ns").write_text(f"{halt_poll_ns}\n")
    (kvm / "kvmclock_periodic_sync").write_text("Y\n")
    (kvm / "tdp_mmu").write_text("Y\n")
    var = root / variant / "parameters"
    var.mkdir(parents=True, exist_ok=True)
    (var / "nested").write_text(f"{nested}\n")


def _mk_vfio(root):
    (root / "vfio_pci").mkdir(parents=True, exist_ok=True)


def _mk_dev_kvm(path, mode=0o660, gid=None):
    path.write_text("")  # use a regular file as a stand-in
    os.chmod(path, mode)
    if gid is not None:
        try:
            os.chown(path, -1, gid)
        except (OSError, PermissionError):
            pass


# --- helpers ----------------------------------------------------

def test_nested_active_helper():
    assert mod._nested_active("Y") is True
    assert mod._nested_active("1") is True
    assert mod._nested_active("N") is False
    assert mod._nested_active(None) is False


# --- read_kvm_intel_amd_params ----------------------------------

def test_read_kvm_intel_amd_missing(tmp_path):
    out = mod.read_kvm_intel_amd_params(str(tmp_path))
    assert out == {"variant": None, "nested": None}


def test_read_kvm_intel_amd_intel(tmp_path):
    _mk_kvm(tmp_path, variant="kvm_intel", nested="Y")
    out = mod.read_kvm_intel_amd_params(str(tmp_path))
    assert out["variant"] == "kvm_intel"
    assert out["nested"] == "Y"


def test_read_kvm_intel_amd_amd(tmp_path):
    _mk_kvm(tmp_path, variant="kvm_amd", nested="1")
    out = mod.read_kvm_intel_amd_params(str(tmp_path))
    assert out["variant"] == "kvm_amd"
    assert out["nested"] == "1"


# --- read_kvm_params --------------------------------------------

def test_read_kvm_params(tmp_path):
    _mk_kvm(tmp_path, halt_poll_ns=500000)
    out = mod.read_kvm_params(str(tmp_path))
    assert out["halt_poll_ns"] == 500000


# --- stat_dev_kvm -----------------------------------------------

def test_stat_dev_kvm_missing(tmp_path):
    out = mod.stat_dev_kvm(str(tmp_path / "no_kvm"))
    assert out == {"present": False}


def test_stat_dev_kvm_present(tmp_path):
    p = tmp_path / "kvm"
    _mk_dev_kvm(p, mode=0o660)
    out = mod.stat_dev_kvm(str(p))
    assert out["present"] is True
    assert out["mode"] == 0o660


# --- classify ---------------------------------------------------

def _ia(variant="kvm_intel", nested="N"):
    return {"variant": variant, "nested": nested}


def _params(halt_poll_ns=200000):
    return {"halt_poll_ns": halt_poll_ns,
              "kvmclock_periodic_sync": "Y", "tdp_mmu": "Y"}


def _dk(present=True, mode=0o660, group_name="kvm"):
    return {"present": present, "mode": mode,
              "uid": 0, "gid": 108, "group_name": group_name}


def test_classify_unknown():
    v = mod.classify(False, _ia(variant=None, nested=None),
                       {}, False, {"present": False})
    assert v["verdict"] == "unknown"


def test_classify_kvm_disabled():
    # /sys/module/kvm present but /dev/kvm missing.
    v = mod.classify(True, _ia(), _params(), False,
                       {"present": False})
    assert v["verdict"] == "kvm_disabled"


def test_classify_ok():
    v = mod.classify(True, _ia(nested="N"), _params(), False,
                       _dk())
    assert v["verdict"] == "ok"


def test_classify_nested_with_passthrough():
    v = mod.classify(True, _ia(nested="Y"), _params(), True,
                       _dk())
    assert v["verdict"] == "nested_on_with_passthrough"


def test_classify_halt_poll_excessive():
    v = mod.classify(True, _ia(nested="N"),
                       _params(halt_poll_ns=600_000),
                       False, _dk())
    assert v["verdict"] == "halt_poll_excessive"


def test_classify_group_perm_world_write():
    v = mod.classify(True, _ia(nested="N"), _params(), False,
                       _dk(mode=0o666))
    assert v["verdict"] == "group_perm_missing"


def test_classify_group_perm_wrong_group():
    v = mod.classify(True, _ia(nested="N"), _params(), False,
                       _dk(group_name="users"))
    assert v["verdict"] == "group_perm_missing"


def test_classify_priority_nested_wins_over_halt_poll():
    v = mod.classify(True, _ia(nested="Y"),
                       _params(halt_poll_ns=600_000),
                       True, _dk())
    assert v["verdict"] == "nested_on_with_passthrough"


# --- status integration -----------------------------------------

def test_status_unknown(tmp_path):
    out = mod.status(None, str(tmp_path / "nomod"),
                       str(tmp_path / "nokvm"))
    assert out["ok"] is False
    assert out["verdict"]["verdict"] == "unknown"


def test_status_live_like(tmp_path):
    sm = tmp_path / "sysmod"
    _mk_kvm(sm, variant="kvm_intel", nested="Y", halt_poll_ns=200000)
    dk = tmp_path / "kvm_node"
    _mk_dev_kvm(dk, mode=0o660)
    out = mod.status(None, str(sm), str(dk))
    assert out["ok"] is True
    # No vfio_pci → ok (not nested_on_with_passthrough)
    assert out["kvm_variant"] == "kvm_intel"
    # Verdict may be group_perm_missing because the test file's
    # group is not 'kvm' on disk — that's a real test-env artifact,
    # so we check it's at least not 'unknown' or 'kvm_disabled'.
    assert out["verdict"]["verdict"] in (
        "ok", "group_perm_missing")
