"""Tests for modules/kernel_oops_warn_counter_audit.py R&D #103.1."""
from __future__ import annotations

import pytest

from gpu_dashboard.modules import kernel_oops_warn_counter_audit as mod


# --- classify --------------------------------------------------

def test_classify_unknown():
    v = mod.classify(False, None, None, None)
    assert v["verdict"] == "unknown"


def test_classify_requires_root():
    v = mod.classify(True, None, None, 1)
    assert v["verdict"] == "requires_root"


def test_classify_ok():
    v = mod.classify(True, 0, 0, 1)
    assert v["verdict"] == "ok"


def test_classify_silent_oops_err():
    v = mod.classify(True, 1, 0, 0)
    assert v["verdict"] == "silent_oops_since_boot"


def test_classify_silent_oops_none_panic():
    # panic_on_oops unreadable → treat as 0 (worst-case)
    v = mod.classify(True, 1, 0, None)
    assert v["verdict"] == "silent_oops_since_boot"


def test_classify_oops_with_panic_doesnt_fire():
    # If panic_on_oops=1, the kernel would have panicked,
    # so any oops_count > 0 here is a stale prior-boot
    # value (impossible since /sys/kernel/oops_count is
    # an atomic counter wiped at boot). Either way, panic=1
    # → no silent_oops verdict.
    v = mod.classify(True, 1, 0, 1)
    assert v["verdict"] != "silent_oops_since_boot"


def test_classify_warn_high():
    v = mod.classify(True, 0, 10, 1)
    assert v["verdict"] == "warn_count_high"


def test_classify_warn_low_accent():
    v = mod.classify(True, 0, 2, 1)
    assert v["verdict"] == "warn_count_nonzero"


# Priority : silent_oops > warn_high > warn_low
def test_priority_oops_over_warn():
    v = mod.classify(True, 1, 10, 0)
    assert v["verdict"] == "silent_oops_since_boot"


def test_priority_warn_high_over_low():
    v = mod.classify(True, 0, 10, 1)
    assert v["verdict"] == "warn_count_high"


# --- status integration ----------------------------------------

def test_status_unknown(tmp_path):
    out = mod.status(None,
                       str(tmp_path / "no_oops"),
                       str(tmp_path / "no_warn"),
                       str(tmp_path / "no_panic"))
    assert out["verdict"]["verdict"] == "unknown"


def test_status_ok_synthetic(tmp_path):
    o = tmp_path / "oops"
    o.write_text("0\n")
    w = tmp_path / "warn"
    w.write_text("0\n")
    p = tmp_path / "panic"
    p.write_text("1\n")
    out = mod.status(None, str(o), str(w), str(p))
    assert out["verdict"]["verdict"] == "ok"
    assert out["panic_on_oops"] == 1


def test_status_silent_oops_synthetic(tmp_path):
    o = tmp_path / "oops"
    o.write_text("3\n")
    w = tmp_path / "warn"
    w.write_text("0\n")
    p = tmp_path / "panic"
    p.write_text("0\n")
    out = mod.status(None, str(o), str(w), str(p))
    assert (out["verdict"]["verdict"]
            == "silent_oops_since_boot")
    assert out["oops_count"] == 3
