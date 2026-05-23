"""Tests for modules/rtc_clock_audit.py — R&D #49.4."""
from __future__ import annotations

import pytest

from gpu_dashboard.modules import rtc_clock_audit as mod


def _mk_rtc(root, name="rtc0", **fields):
    d = root / name
    d.mkdir(parents=True, exist_ok=True)
    for k, v in {"name": "rtc_cmos", "since_epoch": 1779000000,
                  "date": "2026-05-23", "time": "07:00:00",
                  "hctosys": 1, "wakealarm": "",
                  "max_user_freq": 64, **fields}.items():
        (d / k).write_text(str(v) + "\n")


# --- list_rtcs ---------------------------------------------------

def test_list_rtcs_basic(tmp_path):
    _mk_rtc(tmp_path, "rtc0")
    out = mod.list_rtcs(str(tmp_path))
    assert len(out) == 1
    assert out[0]["name"] == "rtc0"
    assert out[0]["rtc_name"] == "rtc_cmos"


def test_list_rtcs_missing(tmp_path):
    assert mod.list_rtcs(str(tmp_path / "nope")) == []


def test_list_rtcs_empty(tmp_path):
    assert mod.list_rtcs(str(tmp_path)) == []


# --- list_pps ----------------------------------------------------

def test_list_pps_missing(tmp_path):
    assert mod.list_pps(str(tmp_path / "nope")) == []


def test_list_pps_basic(tmp_path):
    (tmp_path / "pps0").mkdir()
    (tmp_path / "pps1").mkdir()
    out = mod.list_pps(str(tmp_path))
    assert out == ["pps0", "pps1"]


# --- classify ----------------------------------------------------

def _rtc(since_epoch=1779000000, hctosys=1, name="rtc_cmos"):
    return {"name": "rtc0", "rtc_name": name,
              "since_epoch": since_epoch, "date": "2026-05-23",
              "time": "07:00:00", "hctosys": hctosys,
              "wakealarm": None, "max_user_freq": 64}


def test_classify_no_rtc():
    v = mod.classify([], [])
    assert v["verdict"] == "no_rtc"


def test_classify_ok():
    v = mod.classify([_rtc(since_epoch=1779000000)], [],
                       now_epoch=1779000010)
    assert v["verdict"] == "ok"


def test_classify_rtc_drift_high():
    v = mod.classify([_rtc(since_epoch=1779000000)], [],
                       now_epoch=1779000200)
    assert v["verdict"] == "rtc_drift_high"
    assert "200s" in v["reason"]


def test_classify_drift_skipped_below_threshold():
    v = mod.classify([_rtc(since_epoch=1779000000)], [],
                       now_epoch=1779000059)
    assert v["verdict"] == "ok"


def test_classify_hctosys_disabled():
    v = mod.classify([_rtc(hctosys=0)], [],
                       now_epoch=1779000000)
    assert v["verdict"] == "hctosys_disabled"


def test_classify_priority_drift_wins():
    v = mod.classify([_rtc(since_epoch=1779000000, hctosys=0)], [],
                       now_epoch=1779000200)
    assert v["verdict"] == "rtc_drift_high"


def test_classify_skip_drift_when_no_epoch():
    rtc = _rtc()
    rtc["since_epoch"] = None
    v = mod.classify([rtc], [], now_epoch=1779000000)
    assert v["verdict"] in ("ok", "hctosys_disabled")


# --- status integration ------------------------------------------

def test_status_with_isolated(monkeypatch, tmp_path):
    rtcs_root = tmp_path / "rtc"
    rtcs_root.mkdir()
    _mk_rtc(rtcs_root, "rtc0", since_epoch=int(__import__("time").time()))
    monkeypatch.setattr(mod, "_SYS_CLASS_RTC", str(rtcs_root))
    monkeypatch.setattr(mod, "_SYS_CLASS_PPS",
                        str(tmp_path / "nope"))
    out = mod.status()
    assert out["ok"] is True
    assert out["rtc_count"] == 1
    assert out["verdict"]["verdict"] == "ok"


def test_status_no_rtc(monkeypatch, tmp_path):
    monkeypatch.setattr(mod, "_SYS_CLASS_RTC",
                        str(tmp_path / "nope"))
    monkeypatch.setattr(mod, "_SYS_CLASS_PPS",
                        str(tmp_path / "nope2"))
    out = mod.status()
    assert out["ok"] is False
    assert out["verdict"]["verdict"] == "unknown"
