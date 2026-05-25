"""Tests for modules/rseq_kernel_audit.py R&D #99.4."""
from __future__ import annotations

import gzip

import pytest

from gpu_dashboard.modules import rseq_kernel_audit as mod


# --- parse_config_options --------------------------------------

def test_parse_config_empty():
    out = mod.parse_config_options(
        None, ("CONFIG_RSEQ",))
    assert out == {"CONFIG_RSEQ": None}


def test_parse_config_y():
    text = "CONFIG_RSEQ=y\nCONFIG_OTHER=n\n"
    out = mod.parse_config_options(
        text, ("CONFIG_RSEQ", "CONFIG_FUTEX_PI"))
    assert out["CONFIG_RSEQ"] == "y"
    assert out["CONFIG_FUTEX_PI"] is None


def test_parse_config_is_not_set():
    text = "# CONFIG_DEBUG_RSEQ is not set\n"
    out = mod.parse_config_options(
        text, ("CONFIG_DEBUG_RSEQ",))
    assert out["CONFIG_DEBUG_RSEQ"] == "n"


def test_parse_config_quoted_string():
    # rare for our keys but shouldn't blow up
    text = 'CONFIG_RSEQ="y"\n'
    out = mod.parse_config_options(
        text, ("CONFIG_RSEQ",))
    assert out["CONFIG_RSEQ"] == "y"


# --- find_kernel_config ----------------------------------------

def test_find_config_boot(tmp_path):
    boot = tmp_path / "boot"
    boot.mkdir()
    (boot / "config-6.17.0").write_text(
        "CONFIG_RSEQ=y\n")
    out = mod.find_kernel_config(
        "6.17.0", str(boot),
        str(tmp_path / "no_proc"))
    assert "CONFIG_RSEQ=y" in out


def test_find_config_gz_fallback(tmp_path):
    boot = tmp_path / "boot"
    boot.mkdir()
    gz_path = tmp_path / "config.gz"
    with gzip.open(str(gz_path), "wt") as fh:
        fh.write("CONFIG_RSEQ=y\nCONFIG_FUTEX_PI=y\n")
    out = mod.find_kernel_config(
        "9.99.0", str(boot), str(gz_path))
    assert "CONFIG_FUTEX_PI=y" in out


def test_find_config_missing(tmp_path):
    out = mod.find_kernel_config(
        "1.0", str(tmp_path / "nope"),
        str(tmp_path / "no_proc"))
    assert out is None


# --- classify --------------------------------------------------

def test_classify_unknown():
    v = mod.classify(False, False, None, None, None)
    assert v["verdict"] == "unknown"


def test_classify_requires_root():
    v = mod.classify(True, False, None, None, None)
    assert v["verdict"] == "requires_root"


def test_classify_ok():
    v = mod.classify(True, True, "y", "n", "y")
    assert v["verdict"] == "ok"


def test_classify_rseq_disabled_warn():
    v = mod.classify(True, True, "n", "n", "y")
    assert v["verdict"] == "rseq_kernel_disabled"


def test_classify_futex_pi_disabled_accent():
    v = mod.classify(True, True, "y", "y", "n")
    assert v["verdict"] == "futex_pi_disabled"


def test_classify_debug_rseq_off_is_ok():
    # CONFIG_DEBUG_RSEQ=n is the production default ;
    # not flagged as noise on every shipped kernel.
    v = mod.classify(True, True, "y", "n", "y")
    assert v["verdict"] == "ok"


# Priority : rseq_disabled > futex_pi
def test_priority_rseq_over_futex():
    v = mod.classify(True, True, "n", "n", "n")
    assert v["verdict"] == "rseq_kernel_disabled"


# --- status integration ----------------------------------------

def test_status_ok_synthetic(tmp_path):
    boot = tmp_path / "boot"
    boot.mkdir()
    (boot / "config-6.17.0").write_text(
        "CONFIG_RSEQ=y\n"
        "CONFIG_FUTEX_PI=y\n"
        "# CONFIG_DEBUG_RSEQ is not set\n")
    out = mod.status(None, "6.17.0", str(boot),
                       str(tmp_path / "no_proc"))
    assert out["verdict"]["verdict"] == "ok"
    assert out["CONFIG_RSEQ"] == "y"
    assert out["CONFIG_DEBUG_RSEQ"] == "n"


def test_status_rseq_disabled_synthetic(tmp_path):
    boot = tmp_path / "boot"
    boot.mkdir()
    (boot / "config-6.17.0").write_text(
        "# CONFIG_RSEQ is not set\n"
        "CONFIG_FUTEX_PI=y\n")
    out = mod.status(None, "6.17.0", str(boot),
                       str(tmp_path / "no_proc"))
    assert (out["verdict"]["verdict"]
            == "rseq_kernel_disabled")
    assert out["ok"] is False


def test_status_unknown(tmp_path):
    out = mod.status(None, "6.17.0",
                       str(tmp_path / "no_boot"),
                       str(tmp_path / "no_proc"))
    assert out["verdict"]["verdict"] == "unknown"
