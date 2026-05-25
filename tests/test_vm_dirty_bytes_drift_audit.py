"""Tests for modules/vm_dirty_bytes_drift_audit.py R&D #107.3."""
from __future__ import annotations

import pytest

from gpu_dashboard.modules import vm_dirty_bytes_drift_audit as mod


def test_classify_unknown():
    v = mod.classify(False, None, None, None, None)
    assert v["verdict"] == "unknown"


def test_classify_requires_root():
    v = mod.classify(True, None, None, None, None)
    assert v["verdict"] == "requires_root"


def test_classify_ok_default():
    v = mod.classify(True, 0, 0, 20, 10)
    assert v["verdict"] == "ok"


def test_classify_dirty_bytes_overrides_warn():
    v = mod.classify(True, 2_000_000_000, 0, 20, 10)
    assert v["verdict"] == "dirty_bytes_overrides_ratio"


def test_classify_dirty_bg_bytes_overrides_warn():
    v = mod.classify(True, 0, 500_000_000, 20, 10)
    assert v["verdict"] == "dirty_bg_bytes_overrides_ratio"


# Priority : dirty_bytes > dirty_bg_bytes
def test_priority_dirty_over_bg():
    v = mod.classify(True, 2_000_000_000, 500_000_000,
                          20, 10)
    assert v["verdict"] == "dirty_bytes_overrides_ratio"


def test_status_unknown(tmp_path):
    out = mod.status(None, str(tmp_path / "nope"))
    assert out["verdict"]["verdict"] == "unknown"


def test_status_ok(tmp_path):
    vm = tmp_path / "vm"
    vm.mkdir()
    (vm / "dirty_bytes").write_text("0\n")
    (vm / "dirty_background_bytes").write_text("0\n")
    (vm / "dirty_ratio").write_text("20\n")
    (vm / "dirty_background_ratio").write_text("10\n")
    out = mod.status(None, str(vm))
    assert out["verdict"]["verdict"] == "ok"


def test_status_overridden(tmp_path):
    vm = tmp_path / "vm"
    vm.mkdir()
    (vm / "dirty_bytes").write_text("2147483648\n")
    (vm / "dirty_background_bytes").write_text("0\n")
    (vm / "dirty_ratio").write_text("20\n")
    (vm / "dirty_background_ratio").write_text("10\n")
    out = mod.status(None, str(vm))
    assert (out["verdict"]["verdict"]
            == "dirty_bytes_overrides_ratio")
