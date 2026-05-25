"""Tests for modules/bpf_jit_harden_audit.py R&D #102.4."""
from __future__ import annotations

import pytest

from gpu_dashboard.modules import bpf_jit_harden_audit as mod


# --- classify --------------------------------------------------

def test_classify_unknown():
    v = mod.classify(None, None, None, None, False)
    assert v["verdict"] == "unknown"


def test_classify_requires_root():
    v = mod.classify(None, None, None, 2, True)
    assert v["verdict"] == "requires_root"


def test_classify_ok():
    v = mod.classify(1, 0, 264_000_000, 2, True)
    assert v["verdict"] == "ok"


def test_classify_ok_with_unpriv_disabled():
    # harden=0 but unprivileged_bpf_disabled=2 → safe
    v = mod.classify(0, 0, 264_000_000, 2, True)
    assert v["verdict"] == "ok"


def test_classify_unhardened_unpriv_warn():
    v = mod.classify(0, 0, 264_000_000, 0, True)
    assert v["verdict"] == "bpf_jit_unhardened_unpriv"


def test_classify_unhardened_unpriv_1_warn():
    # disabled=1 = only-root-CAP_SYS_ADMIN ; still allows
    # some unpriv? Treat != 2 as warn.
    v = mod.classify(0, 0, 264_000_000, 1, True)
    assert v["verdict"] == "bpf_jit_unhardened_unpriv"


def test_classify_kallsyms_leak_accent():
    v = mod.classify(2, 1, 264_000_000, 2, True)
    assert v["verdict"] == "bpf_jit_kallsyms_leak"


# Priority : unpriv > kallsyms
def test_priority_unpriv_over_kallsyms():
    v = mod.classify(0, 1, 264_000_000, 0, True)
    assert v["verdict"] == "bpf_jit_unhardened_unpriv"


# --- status integration ----------------------------------------

def test_status_unknown(tmp_path):
    out = mod.status(None,
                       str(tmp_path / "no_net_core"),
                       str(tmp_path / "no_kernel"))
    assert out["verdict"]["verdict"] == "unknown"


def test_status_ok_synthetic(tmp_path):
    nc = tmp_path / "net_core"
    nc.mkdir()
    (nc / "bpf_jit_harden").write_text("2\n")
    (nc / "bpf_jit_kallsyms").write_text("0\n")
    (nc / "bpf_jit_limit").write_text("264000000\n")
    k = tmp_path / "kernel"
    k.mkdir()
    (k / "unprivileged_bpf_disabled").write_text("2\n")
    out = mod.status(None, str(nc), str(k))
    assert out["verdict"]["verdict"] == "ok"
    assert out["bpf_jit_harden"] == 2


def test_status_unhardened_unpriv(tmp_path):
    nc = tmp_path / "net_core"
    nc.mkdir()
    (nc / "bpf_jit_harden").write_text("0\n")
    (nc / "bpf_jit_kallsyms").write_text("0\n")
    (nc / "bpf_jit_limit").write_text("264000000\n")
    k = tmp_path / "kernel"
    k.mkdir()
    (k / "unprivileged_bpf_disabled").write_text("0\n")
    out = mod.status(None, str(nc), str(k))
    assert (out["verdict"]["verdict"]
            == "bpf_jit_unhardened_unpriv")
