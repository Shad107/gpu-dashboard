"""Tests for modules/abi_compat_audit.py — R&D #74.3."""
from __future__ import annotations

import pytest

from gpu_dashboard.modules import abi_compat_audit as mod


def _mk_abi(root, **knobs):
    root.mkdir(parents=True, exist_ok=True)
    for k, v in knobs.items():
        (root / k).write_text(str(v) + "\n")


# --- scan_abi ---------------------------------------------------

def test_scan_missing(tmp_path):
    assert mod.scan_abi(str(tmp_path / "nope")) == {}


def test_scan(tmp_path):
    _mk_abi(tmp_path, vsyscall32=1, legacy_va_layout=0)
    out = mod.scan_abi(str(tmp_path))
    assert out == {"vsyscall32": 1, "legacy_va_layout": 0}


# --- classify ---------------------------------------------------

def test_classify_unknown():
    v = mod.classify(False, {}, None, None)
    assert v["verdict"] == "unknown"


def test_classify_ok():
    v = mod.classify(True, {"vsyscall32": 1}, None, "enabled")
    assert v["verdict"] == "ok"


def test_classify_vsyscall32_disabled():
    v = mod.classify(True, {"vsyscall32": 0}, 1, "enabled")
    assert v["verdict"] == "vsyscall32_disabled_breaks_steam"


def test_classify_ia32_off():
    v = mod.classify(True, {"vsyscall32": 1}, 0, "enabled")
    assert v["verdict"] == "ia32_emulation_off"


def test_classify_legacy_va_layout():
    v = mod.classify(True,
                          {"vsyscall32": 1, "legacy_va_layout": 1},
                          None, "enabled")
    assert v["verdict"] == "legacy_va_layout_forced"


def test_classify_binfmt_disabled():
    v = mod.classify(True, {"vsyscall32": 1}, None, "disabled")
    assert v["verdict"] == "nonstandard_abi_quirks"


def test_classify_unknown_knob():
    v = mod.classify(True,
                          {"vsyscall32": 1,
                            "uapi_version": 5},
                          None, "enabled")
    assert v["verdict"] == "nonstandard_abi_quirks"


def test_classify_non_default_knob():
    # x32_emulation default is 1, value 0 = quirk
    v = mod.classify(True,
                          {"vsyscall32": 1,
                            "x32_emulation": 0},
                          None, "enabled")
    assert v["verdict"] == "nonstandard_abi_quirks"


# Priority : vsyscall32 > ia32 > legacy_va > quirks
def test_priority_vsyscall32_over_ia32():
    v = mod.classify(True, {"vsyscall32": 0}, 0, "enabled")
    assert v["verdict"] == "vsyscall32_disabled_breaks_steam"


def test_priority_ia32_over_legacy_va():
    v = mod.classify(True,
                          {"vsyscall32": 1, "legacy_va_layout": 1},
                          0, "enabled")
    assert v["verdict"] == "ia32_emulation_off"


def test_priority_legacy_va_over_quirks():
    v = mod.classify(True,
                          {"vsyscall32": 1,
                            "legacy_va_layout": 1,
                            "x32_emulation": 0},
                          None, "enabled")
    assert v["verdict"] == "legacy_va_layout_forced"


# --- status integration ----------------------------------------

def test_status_unknown(tmp_path):
    out = mod.status(None,
                          str(tmp_path / "no_abi"),
                          str(tmp_path / "no_kernel"),
                          str(tmp_path / "no_binfmt"))
    assert out["ok"] is False
    assert out["verdict"]["verdict"] == "unknown"


def test_status_ok_synthetic(tmp_path):
    abi = tmp_path / "abi"
    _mk_abi(abi, vsyscall32=1)
    binfmt = tmp_path / "status"
    binfmt.write_text("enabled\n")
    out = mod.status(None, str(abi),
                          str(tmp_path / "no_kernel"),
                          str(binfmt))
    assert out["ok"] is True
    assert out["abi_knobs"] == {"vsyscall32": 1}
    assert out["verdict"]["verdict"] == "ok"


def test_status_vsyscall32_disabled(tmp_path):
    abi = tmp_path / "abi"
    _mk_abi(abi, vsyscall32=0)
    binfmt = tmp_path / "status"; binfmt.write_text("enabled\n")
    out = mod.status(None, str(abi),
                          str(tmp_path / "no_kernel"),
                          str(binfmt))
    assert out["verdict"]["verdict"] == \
        "vsyscall32_disabled_breaks_steam"
