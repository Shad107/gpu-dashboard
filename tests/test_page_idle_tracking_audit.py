"""Tests for modules/page_idle_tracking_audit.py — R&D #71.3."""
from __future__ import annotations

import os

import pytest

from gpu_dashboard.modules import page_idle_tracking_audit as mod


# --- classify ---------------------------------------------------

def test_classify_page_idle_disabled():
    v = mod.classify(False, False, None, False, None, None)
    assert v["verdict"] == "page_idle_disabled"


def test_classify_bitmap_unreadable():
    v = mod.classify(True, True, False, True, True, 3)
    assert v["verdict"] == "bitmap_unreadable"


def test_classify_requires_root_kpagecount():
    v = mod.classify(True, True, True, True, False, 3)
    assert v["verdict"] == "requires_root"


def test_classify_unknown_no_bitmap_no_count():
    v = mod.classify(True, False, None, False, None, 3)
    assert v["verdict"] == "unknown"


def test_classify_ok():
    v = mod.classify(True, True, True, True, True, 3)
    assert v["verdict"] == "ok"


# Priority : disabled > bitmap_unreadable > requires_root
def test_priority_disabled_over_bitmap():
    v = mod.classify(False, True, False, True, False, 3)
    assert v["verdict"] == "page_idle_disabled"


def test_priority_bitmap_over_kpagecount():
    v = mod.classify(True, True, False, True, False, 3)
    assert v["verdict"] == "bitmap_unreadable"


# --- status integration ----------------------------------------

def test_status_disabled(tmp_path):
    out = mod.status(None,
                          str(tmp_path / "no_page_idle"),
                          str(tmp_path / "no_bitmap"),
                          str(tmp_path / "no_count"),
                          str(tmp_path / "no_cluster"))
    assert out["ok"] is False
    assert out["verdict"]["verdict"] == "page_idle_disabled"


def test_status_ok_synthetic(tmp_path):
    sys_page_idle = tmp_path / "page_idle"
    sys_page_idle.mkdir()
    bitmap = sys_page_idle / "bitmap"
    bitmap.write_bytes(b"\x00" * 64)
    kpagecount = tmp_path / "kpagecount"
    kpagecount.write_bytes(b"\x00" * 64)
    page_cluster = tmp_path / "page-cluster"
    page_cluster.write_text("3\n")
    out = mod.status(None, str(sys_page_idle),
                          str(bitmap), str(kpagecount),
                          str(page_cluster))
    assert out["ok"] is True
    assert out["bitmap_present"] is True
    assert out["bitmap_readable"] is True
    assert out["page_cluster"] == 3
    assert out["verdict"]["verdict"] == "ok"


def test_status_bitmap_eacces_synthetic(tmp_path):
    sys_page_idle = tmp_path / "page_idle"
    sys_page_idle.mkdir()
    bitmap = sys_page_idle / "bitmap"
    bitmap.write_bytes(b"\x00" * 64)
    os.chmod(str(bitmap), 0o000)
    page_cluster = tmp_path / "page-cluster"
    page_cluster.write_text("3\n")
    out = mod.status(None, str(sys_page_idle),
                          str(bitmap),
                          str(tmp_path / "no_count"),
                          str(page_cluster))
    # When running as root the chmod won't actually block reads.
    # Skip the assertion when we're root.
    if os.geteuid() == 0:
        pytest.skip("Running as root; chmod 000 doesn't block.")
    assert out["bitmap_readable"] is False
    assert out["verdict"]["verdict"] == "bitmap_unreadable"
