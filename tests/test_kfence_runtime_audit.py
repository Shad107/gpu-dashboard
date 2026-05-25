"""Tests for modules/kfence_runtime_audit.py R&D #101.1."""
from __future__ import annotations

import gzip
import pytest

from gpu_dashboard.modules import kfence_runtime_audit as mod


# --- find_config_sample_interval -------------------------------

def test_find_config_boot(tmp_path):
    boot = tmp_path / "boot"
    boot.mkdir()
    (boot / "config-6.17.0").write_text(
        "CONFIG_KFENCE=y\n"
        "CONFIG_KFENCE_SAMPLE_INTERVAL=0\n")
    out = mod.find_config_sample_interval(
        "6.17.0", str(boot),
        str(tmp_path / "no_proc"))
    assert out == 0


def test_find_config_proc_gz(tmp_path):
    boot = tmp_path / "boot"
    boot.mkdir()
    gz = tmp_path / "config.gz"
    with gzip.open(str(gz), "wt") as fh:
        fh.write("CONFIG_KFENCE_SAMPLE_INTERVAL=500\n")
    out = mod.find_config_sample_interval(
        "9.99.0", str(boot), str(gz))
    assert out == 500


def test_find_config_missing(tmp_path):
    out = mod.find_config_sample_interval(
        "1.0", str(tmp_path / "no_boot"),
        str(tmp_path / "no_proc"))
    assert out is None


# --- classify --------------------------------------------------

def test_classify_unknown():
    v = mod.classify(False, None, None)
    assert v["verdict"] == "unknown"


def test_classify_requires_root():
    v = mod.classify(True, None, None)
    assert v["verdict"] == "requires_root"


def test_classify_ok_from_sysfs():
    v = mod.classify(True, 200, 0)
    # sysfs wins over CONFIG
    assert v["verdict"] == "ok"


def test_classify_disabled_warn_from_sysfs():
    v = mod.classify(True, 0, 500)
    assert v["verdict"] == "kfence_disabled"


def test_classify_disabled_warn_from_config():
    # sysfs unreadable, CONFIG=0 → warn
    v = mod.classify(True, None, 0)
    assert v["verdict"] == "kfence_disabled"


def test_classify_high_accent():
    v = mod.classify(True, 5000, 0)
    assert v["verdict"] == "kfence_sample_interval_high"


# Priority : disabled > high
def test_priority_disabled_over_high():
    # if effective is 0, even if CONFIG bakes a high value
    # we treat disabled
    v = mod.classify(True, 0, 5000)
    assert v["verdict"] == "kfence_disabled"


# --- status integration ----------------------------------------

def test_status_unknown(tmp_path):
    out = mod.status(None, str(tmp_path / "no_sysfs"),
                       str(tmp_path / "no_boot"),
                       str(tmp_path / "no_proc"),
                       "6.17.0")
    assert out["verdict"]["verdict"] == "unknown"


def test_status_disabled_from_config(tmp_path):
    d = tmp_path / "params"
    d.mkdir()
    # No sample_interval file → unreadable
    boot = tmp_path / "boot"
    boot.mkdir()
    (boot / "config-6.17.0").write_text(
        "CONFIG_KFENCE_SAMPLE_INTERVAL=0\n")
    out = mod.status(None, str(d), str(boot),
                       str(tmp_path / "no_proc"),
                       "6.17.0")
    assert out["verdict"]["verdict"] == "kfence_disabled"
    assert out["config_sample_interval"] == 0


def test_status_ok_synthetic(tmp_path):
    d = tmp_path / "params"
    d.mkdir()
    (d / "sample_interval").write_text("200\n")
    (d / "skip_covered_thresh").write_text("75\n")
    out = mod.status(None, str(d),
                       str(tmp_path / "no_boot"),
                       str(tmp_path / "no_proc"),
                       "6.17.0")
    assert out["verdict"]["verdict"] == "ok"
    assert out["sample_interval"] == 200
