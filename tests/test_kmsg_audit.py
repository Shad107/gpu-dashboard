"""Tests for modules/kmsg_audit.py — R&D #49.1."""
from __future__ import annotations

import pytest

from gpu_dashboard.modules import kmsg_audit as mod


# --- parse_printk -------------------------------------------------

def test_parse_printk_basic():
    p = mod.parse_printk("4\t4\t1\t7\n")
    assert p["console_loglevel"] == 4
    assert p["default_console_loglevel"] == 7


def test_parse_printk_empty():
    assert mod.parse_printk("") == {}
    assert mod.parse_printk(None) == {}


def test_parse_printk_short():
    assert mod.parse_printk("4 4") == {}


# --- parse_kmsg_line ----------------------------------------------

def test_parse_kmsg_basic():
    r = mod.parse_kmsg_line(
        "6,1234,5678901,-;Linux version 6.17.0\n")
    assert r is not None
    assert r["priority"] == 6
    assert r["level"] == 6  # info
    assert r["facility"] == 0
    assert r["seq"] == 1234
    assert r["timestamp_usec"] == 5678901
    assert r["message"] == "Linux version 6.17.0"


def test_parse_kmsg_err_level():
    r = mod.parse_kmsg_line("3,100,200,-;some error")
    assert r["level"] == 3


def test_parse_kmsg_with_facility():
    # priority = facility 1 (user) * 8 + level 4 = 12
    r = mod.parse_kmsg_line("12,1,1,-;message")
    assert r["facility"] == 1
    assert r["level"] == 4


def test_parse_kmsg_malformed():
    assert mod.parse_kmsg_line("") is None
    assert mod.parse_kmsg_line("no semicolon") is None
    assert mod.parse_kmsg_line("a,b,c,d;msg") is None
    assert mod.parse_kmsg_line("1,2;short") is None


# --- classify -----------------------------------------------------

def _printk():
    return {"console_loglevel": 4, "default_message_loglevel": 4,
              "minimum_console_loglevel": 1,
              "default_console_loglevel": 7}


def _kmsg(records=100, suppressed=0, by_level=None):
    return {"available": True, "permission_error": False,
              "records_read": records, "suppressed_count": suppressed,
              "by_level": by_level or {}}


def test_classify_unknown_no_printk():
    v = mod.classify({}, {})
    assert v["verdict"] == "unknown"


def test_classify_requires_root():
    v = mod.classify(_printk(),
                       {"available": False, "permission_error": True})
    assert v["verdict"] == "requires_root"


def test_classify_ok():
    v = mod.classify(_printk(),
                       _kmsg(records=100, by_level={6: 80, 7: 20}))
    assert v["verdict"] == "ok"


def test_classify_ratelimit_drops():
    v = mod.classify(_printk(),
                       _kmsg(suppressed=3))
    assert v["verdict"] == "ratelimit_drops"


def test_classify_loud_kernel():
    # 5 warn + 3 err = 8 noisy records
    v = mod.classify(_printk(),
                       _kmsg(by_level={3: 3, 4: 5, 6: 50}))
    assert v["verdict"] == "loud_kernel"


def test_classify_loud_skipped_below_threshold():
    # 2 warn + 2 err = 4 < 5 threshold
    v = mod.classify(_printk(),
                       _kmsg(by_level={3: 2, 4: 2, 6: 50}))
    assert v["verdict"] == "ok"


def test_classify_priority_ratelimit_over_loud():
    v = mod.classify(_printk(),
                       _kmsg(suppressed=1,
                              by_level={3: 10, 4: 10}))
    assert v["verdict"] == "ratelimit_drops"


# --- status integration (file isolation harder for /dev/kmsg) ----

def test_status_requires_root_when_no_kmsg(monkeypatch, tmp_path):
    sysk = tmp_path / "k"
    sysk.mkdir()
    (sysk / "printk").write_text("4\t4\t1\t7\n")
    monkeypatch.setattr(mod, "_PROC_SYS_KERNEL", str(sysk))
    monkeypatch.setattr(mod, "_DEV_KMSG", str(tmp_path / "no_kmsg"))
    out = mod.status()
    assert out["ok"] is True
    assert out["verdict"]["verdict"] == "requires_root"


def test_status_unknown(monkeypatch, tmp_path):
    monkeypatch.setattr(mod, "_PROC_SYS_KERNEL",
                        str(tmp_path / "nope"))
    monkeypatch.setattr(mod, "_DEV_KMSG", str(tmp_path / "nokmsg"))
    out = mod.status()
    assert out["verdict"]["verdict"] in ("unknown", "requires_root")
