"""Tests for modules/vfs_limits_audit.py — R&D #46.3."""
from __future__ import annotations

import pytest

from gpu_dashboard.modules import vfs_limits_audit as mod


# --- parse_file_nr ------------------------------------------------

def test_parse_file_nr_basic():
    out = mod.parse_file_nr("18190\t0\t9223372036854775807\n")
    assert out["allocated"] == 18190
    assert out["free"] == 0
    assert out["max"] == 9223372036854775807


def test_parse_file_nr_short():
    assert mod.parse_file_nr("100 0") == {}


def test_parse_file_nr_empty():
    assert mod.parse_file_nr("") == {}
    assert mod.parse_file_nr(None) == {}


# --- read_limits --------------------------------------------------

def test_read_limits_basic(tmp_path):
    (tmp_path / "file-nr").write_text("100\t0\t1000\n")
    (tmp_path / "file-max").write_text("1000\n")
    (tmp_path / "nr_open").write_text("1048576\n")
    (tmp_path / "aio-nr").write_text("10\n")
    (tmp_path / "aio-max-nr").write_text("65536\n")
    out = mod.read_limits(str(tmp_path))
    assert out["file_nr"] == {"allocated": 100, "free": 0, "max": 1000}
    assert out["file_max"] == 1000
    assert out["aio_max_nr"] == 65536


def test_read_limits_missing(tmp_path):
    assert mod.read_limits(str(tmp_path / "nope")) == {}


# --- classify ------------------------------------------------------

def _limits(alloc=100, max_=1000, aio_nr=0, aio_max=65536,
              nr_open=1048576):
    return {"file_nr": {"allocated": alloc, "free": 0, "max": max_},
              "file_max": max_, "nr_open": nr_open,
              "aio_nr": aio_nr, "aio_max_nr": aio_max}


def test_classify_unknown_when_empty():
    v = mod.classify({})
    assert v["verdict"] == "unknown"


def test_classify_ok():
    v = mod.classify(_limits())
    assert v["verdict"] == "ok"


def test_classify_file_nr_high():
    v = mod.classify(_limits(alloc=900, max_=1000))
    assert v["verdict"] == "file_nr_high"
    assert "900" in v["reason"]


def test_classify_aio_nr_high():
    v = mod.classify(_limits(aio_nr=60000, aio_max=65536))
    assert v["verdict"] == "aio_nr_high"


def test_classify_file_wins_over_aio():
    v = mod.classify(_limits(alloc=900, max_=1000,
                                aio_nr=60000, aio_max=65536))
    assert v["verdict"] == "file_nr_high"


def test_classify_ignores_below_threshold():
    v = mod.classify(_limits(alloc=500, max_=1000))
    assert v["verdict"] == "ok"


# --- status integration -------------------------------------------

def test_status_with_isolated(monkeypatch, tmp_path):
    (tmp_path / "file-nr").write_text("100\t0\t1000\n")
    (tmp_path / "file-max").write_text("1000\n")
    (tmp_path / "nr_open").write_text("1048576\n")
    monkeypatch.setattr(mod, "_PROC_SYS_FS", str(tmp_path))
    out = mod.status()
    assert out["ok"] is True
    assert out["verdict"]["verdict"] == "ok"


def test_status_unknown(monkeypatch, tmp_path):
    monkeypatch.setattr(mod, "_PROC_SYS_FS", str(tmp_path / "nope"))
    out = mod.status()
    assert out["ok"] is False
    assert out["verdict"]["verdict"] == "unknown"
