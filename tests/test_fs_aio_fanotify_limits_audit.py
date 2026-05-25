"""Tests for modules/fs_aio_fanotify_limits_audit.py R&D #94.2."""
from __future__ import annotations

import pytest

from gpu_dashboard.modules import (
    fs_aio_fanotify_limits_audit as mod)


def _mk_proc_sys_fs(tmp_path, *, aio_max_nr="65536",
                      max_queued_events="16384",
                      max_user_groups="128",
                      max_user_marks="259997"):
    d = tmp_path / "fs"
    d.mkdir(parents=True, exist_ok=True)
    if aio_max_nr is not None:
        (d / "aio-max-nr").write_text(aio_max_nr + "\n")
    fa = d / "fanotify"
    fa.mkdir(exist_ok=True)
    if max_queued_events is not None:
        (fa / "max_queued_events").write_text(
            max_queued_events + "\n")
    if max_user_groups is not None:
        (fa / "max_user_groups").write_text(
            max_user_groups + "\n")
    if max_user_marks is not None:
        (fa / "max_user_marks").write_text(
            max_user_marks + "\n")
    return str(d)


def _mk_meminfo(tmp_path, mem_kib=32 * 2**20):
    p = tmp_path / "meminfo"
    p.write_text(f"MemTotal: {mem_kib} kB\n")
    return str(p)


# --- read_limits -----------------------------------------------

def test_read_limits_missing(tmp_path):
    out = mod.read_limits(str(tmp_path / "nope"))
    assert out["aio_max_nr"] is None
    assert out["max_user_marks"] is None


def test_read_limits_full(tmp_path):
    r = _mk_proc_sys_fs(tmp_path)
    out = mod.read_limits(r)
    assert out["aio_max_nr"] == 65536
    assert out["max_user_marks"] == 259997


# --- classify --------------------------------------------------

_BIG_RAM = 32 * 2**30
_SMALL_RAM = 4 * 2**30


def _limits(**overrides):
    base = {
        "aio_max_nr": 1_048_576,  # bumped
        "max_queued_events": 16384,
        "max_user_groups": 128,
        "max_user_marks": 259997,
    }
    base.update(overrides)
    return base


def test_classify_unknown_all_none():
    v = mod.classify({"aio_max_nr": None,
                          "max_user_marks": None}, None)
    assert v["verdict"] == "unknown"


def test_classify_ok():
    v = mod.classify(_limits(), _BIG_RAM)
    assert v["verdict"] == "ok"


def test_classify_aio_default():
    v = mod.classify(_limits(aio_max_nr=65536),
                          _BIG_RAM)
    assert v["verdict"] == "aio_max_default_low"


def test_classify_aio_below_default_also_low():
    v = mod.classify(_limits(aio_max_nr=32768),
                          _BIG_RAM)
    assert v["verdict"] == "aio_max_default_low"


def test_classify_fanotify_marks_low_big_box():
    v = mod.classify(_limits(max_user_marks=8192),
                          _BIG_RAM)
    assert v["verdict"] == "fanotify_marks_low"


def test_classify_fanotify_marks_low_small_box_is_ok():
    # marks < threshold but on a small box → not a fault
    v = mod.classify(_limits(max_user_marks=8192),
                          _SMALL_RAM)
    assert v["verdict"] == "ok"


# Priority : fanotify_marks_low > aio_max_default_low
def test_priority_fanotify_over_aio():
    v = mod.classify(_limits(
        aio_max_nr=65536, max_user_marks=8192),
                          _BIG_RAM)
    assert v["verdict"] == "fanotify_marks_low"


# --- status integration ----------------------------------------

def test_status_unknown(tmp_path):
    out = mod.status(None,
                       str(tmp_path / "nope"),
                       str(tmp_path / "no_mem"))
    assert out["verdict"]["verdict"] == "unknown"


def test_status_aio_default_synthetic(tmp_path):
    r = _mk_proc_sys_fs(tmp_path, aio_max_nr="65536")
    m = _mk_meminfo(tmp_path)
    out = mod.status(None, r, m)
    assert out["verdict"]["verdict"] == "aio_max_default_low"


def test_status_ok_synthetic(tmp_path):
    r = _mk_proc_sys_fs(tmp_path, aio_max_nr="1048576")
    m = _mk_meminfo(tmp_path)
    out = mod.status(None, r, m)
    assert out["verdict"]["verdict"] == "ok"


def test_status_fanotify_low_synthetic(tmp_path):
    r = _mk_proc_sys_fs(tmp_path,
                              aio_max_nr="1048576",
                              max_user_marks="8192")
    m = _mk_meminfo(tmp_path)
    out = mod.status(None, r, m)
    assert out["verdict"]["verdict"] == "fanotify_marks_low"
    assert out["ok"] is False
