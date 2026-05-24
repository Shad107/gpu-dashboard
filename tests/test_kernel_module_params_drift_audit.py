"""Tests for modules/kernel_module_params_drift_audit.py
— R&D #84.3."""
from __future__ import annotations

import pytest

from gpu_dashboard.modules import kernel_module_params_drift_audit as mod


def _mk_param(root, module, name, value):
    d = root / module / "parameters"
    d.mkdir(parents=True, exist_ok=True)
    (d / name).write_text(str(value) + "\n")


# --- _is_default -----------------------------------------------

def test_is_default_scalar_match():
    assert mod._is_default("30", "30") is True


def test_is_default_scalar_mismatch():
    assert mod._is_default("99", "30") is False


def test_is_default_tuple_match():
    assert mod._is_default("Y", ("Y", "1")) is True
    assert mod._is_default("1", ("Y", "1")) is True


def test_is_default_tuple_mismatch():
    assert mod._is_default("N", ("Y", "1")) is False


def test_is_default_none():
    assert mod._is_default(None, "0") is True


# --- scan ------------------------------------------------------

def test_scan_empty(tmp_path):
    assert mod.scan(str(tmp_path / "nope")) == []


def test_scan_usbcore_default(tmp_path):
    _mk_param(tmp_path, "usbcore",
                "authorized_default", "1")
    _mk_param(tmp_path, "usbcore", "autosuspend", "2")
    out = mod.scan(str(tmp_path))
    assert len(out) == 2
    assert all(not s["non_default"] for s in out)


def test_scan_drift(tmp_path):
    _mk_param(tmp_path, "usbcore",
                "authorized_default", "-1")
    out = mod.scan(str(tmp_path))
    drift = [s for s in out
              if s["param"] == "authorized_default"]
    assert drift[0]["non_default"] is True


def test_scan_skips_missing_module(tmp_path):
    _mk_param(tmp_path, "usbcore",
                "authorized_default", "1")
    out = mod.scan(str(tmp_path))
    # Only usbcore present → only its tracked params
    modules = {s["module"] for s in out}
    assert modules == {"usbcore"}


# --- classify --------------------------------------------------

def test_classify_unknown():
    v = mod.classify([])
    assert v["verdict"] == "unknown"


def _scan(module, param, value, risk, default, non_default):
    return {"module": module, "param": param,
              "value": value, "risk": risk,
              "default": default,
              "non_default": non_default}


def test_classify_ok_all_default():
    v = mod.classify([
        _scan("usbcore", "authorized_default", "1",
                "err", "1", False),
        _scan("zswap", "enabled", "Y", "warn", "Y", False),
    ])
    assert v["verdict"] == "ok"


def test_classify_security_flipped():
    v = mod.classify([
        _scan("usbcore", "authorized_default", "-1",
                "err", "1", True),
    ])
    assert v["verdict"] == "security_param_flipped"


def test_classify_perf_drifted():
    v = mod.classify([
        _scan("zswap", "enabled", "N", "warn", "Y", True),
    ])
    assert v["verdict"] == "perf_param_drifted"


def test_classify_many_non_default():
    v = mod.classify([
        _scan("zswap", "compressor", "lz4",
                "accent", "zstd", True),
        _scan("ksm", "run", "1", "accent", "0", True),
        _scan("xhci_hcd", "link_quirk", "1",
                "accent", "0", True),
    ])
    assert v["verdict"] == "many_non_default"


def test_classify_few_accent_drifts_ok():
    v = mod.classify([
        _scan("ksm", "run", "1", "accent", "0", True),
        _scan("xhci_hcd", "link_quirk", "1",
                "accent", "0", True),
    ])
    assert v["verdict"] == "ok"


# Priority : security > perf > many_non_default
def test_priority_security_over_perf():
    v = mod.classify([
        _scan("usbcore", "authorized_default", "-1",
                "err", "1", True),
        _scan("zswap", "enabled", "N", "warn", "Y", True),
    ])
    assert v["verdict"] == "security_param_flipped"


def test_priority_perf_over_many():
    v = mod.classify([
        _scan("zswap", "enabled", "N", "warn", "Y", True),
        _scan("ksm", "run", "1", "accent", "0", True),
        _scan("xhci_hcd", "link_quirk", "1",
                "accent", "0", True),
        _scan("i915", "enable_psr", "5",
                "accent", "0", True),
    ])
    assert v["verdict"] == "perf_param_drifted"


# --- status integration ----------------------------------------

def test_status_unknown(tmp_path):
    out = mod.status(None, str(tmp_path / "nope"))
    assert out["ok"] is False
    assert out["verdict"]["verdict"] == "unknown"


def test_status_ok_synthetic(tmp_path):
    _mk_param(tmp_path, "usbcore",
                "authorized_default", "1")
    _mk_param(tmp_path, "zswap", "enabled", "Y")
    out = mod.status(None, str(tmp_path))
    assert out["ok"] is True
    assert out["verdict"]["verdict"] == "ok"


def test_status_security_flipped_synthetic(tmp_path):
    _mk_param(tmp_path, "usbcore",
                "authorized_default", "-1")
    out = mod.status(None, str(tmp_path))
    assert out["ok"] is False
    assert (out["verdict"]["verdict"]
            == "security_param_flipped")
