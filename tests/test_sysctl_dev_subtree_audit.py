"""Tests for modules/sysctl_dev_subtree_audit.py — R&D #73.2."""
from __future__ import annotations

import pytest

from gpu_dashboard.modules import sysctl_dev_subtree_audit as mod


def _mk_knob(root, *parts, value):
    p = root.joinpath(*parts)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(str(value) + "\n")


# --- scan -------------------------------------------------------

def test_scan_empty(tmp_path):
    out = mod.scan(str(tmp_path))
    assert out == {"scsi_logging_level": None,
                      "i915_perf_stream_paranoid": None,
                      "hpet_max_user_freq": None,
                      "cdrom_autoclose": None}


def test_scan_typical(tmp_path):
    _mk_knob(tmp_path, "scsi", "logging_level", value=0)
    _mk_knob(tmp_path, "hpet", "max-user-freq", value=64)
    _mk_knob(tmp_path, "cdrom", "autoclose", value=1)
    out = mod.scan(str(tmp_path))
    assert out["scsi_logging_level"] == 0
    assert out["hpet_max_user_freq"] == 64
    assert out["cdrom_autoclose"] == 1


# --- classify ---------------------------------------------------

def _knobs(**overrides):
    base = {"scsi_logging_level": 0,
              "i915_perf_stream_paranoid": None,
              "hpet_max_user_freq": 2048,
              "cdrom_autoclose": 1}
    base.update(overrides)
    return base


def test_classify_unknown():
    v = mod.classify(False, {})
    assert v["verdict"] == "unknown"


def test_classify_ok():
    v = mod.classify(True, _knobs())
    assert v["verdict"] == "ok"


def test_classify_scsi_verbose():
    v = mod.classify(True, _knobs(scsi_logging_level=0xff))
    assert v["verdict"] == "scsi_verbose_logging_on"


def test_classify_i915_paranoid():
    v = mod.classify(True,
                          _knobs(i915_perf_stream_paranoid=1))
    assert v["verdict"] == "i915_perf_paranoid_unset"


def test_classify_i915_paranoid_zero_ok():
    v = mod.classify(True,
                          _knobs(i915_perf_stream_paranoid=0))
    assert v["verdict"] == "ok"


def test_classify_hpet_low():
    v = mod.classify(True, _knobs(hpet_max_user_freq=64))
    assert v["verdict"] == "hpet_max_user_freq_low"


def test_classify_cdrom_off():
    v = mod.classify(True, _knobs(cdrom_autoclose=0))
    assert v["verdict"] == "cdrom_autoclose_off"


# Priority : scsi > i915 > hpet > cdrom
def test_priority_scsi_over_i915():
    v = mod.classify(True,
                          _knobs(scsi_logging_level=1,
                                    i915_perf_stream_paranoid=1))
    assert v["verdict"] == "scsi_verbose_logging_on"


def test_priority_i915_over_hpet():
    v = mod.classify(True,
                          _knobs(i915_perf_stream_paranoid=1,
                                    hpet_max_user_freq=64))
    assert v["verdict"] == "i915_perf_paranoid_unset"


def test_priority_hpet_over_cdrom():
    v = mod.classify(True,
                          _knobs(hpet_max_user_freq=64,
                                    cdrom_autoclose=0))
    assert v["verdict"] == "hpet_max_user_freq_low"


# --- status integration -----------------------------------------

def test_status_unknown(tmp_path):
    out = mod.status(None, str(tmp_path / "nope"))
    assert out["ok"] is False
    assert out["verdict"]["verdict"] == "unknown"


def test_status_ok_synthetic(tmp_path):
    _mk_knob(tmp_path, "scsi", "logging_level", value=0)
    _mk_knob(tmp_path, "hpet", "max-user-freq", value=2048)
    _mk_knob(tmp_path, "cdrom", "autoclose", value=1)
    out = mod.status(None, str(tmp_path))
    assert out["ok"] is True
    assert out["verdict"]["verdict"] == "ok"


def test_status_hpet_low_synthetic(tmp_path):
    _mk_knob(tmp_path, "scsi", "logging_level", value=0)
    _mk_knob(tmp_path, "hpet", "max-user-freq", value=64)
    _mk_knob(tmp_path, "cdrom", "autoclose", value=1)
    out = mod.status(None, str(tmp_path))
    assert out["verdict"]["verdict"] == "hpet_max_user_freq_low"
