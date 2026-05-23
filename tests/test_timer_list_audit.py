"""Tests for modules/timer_list_audit.py — R&D #67.4."""
from __future__ import annotations

import os

import pytest

from gpu_dashboard.modules import timer_list_audit as mod


def _write_timer_list(path, text):
    with open(path, "w") as f:
        f.write(text)


SAMPLE_HEALTHY = """\
Timer List Version: v0.9
HRTIMER_MAX_CLOCK_BASES: 8
now at 1234567890 nsecs

cpu: 0
 .expires_next   :   12345 nsecs
 .hres_active    : 1
 .tick_stopped   : 1
 #0: <0xffff>, hrtimer_wakeup, S:01, do_nanosleep, sleep/1234, active
 #1: <0xffff>, hrtimer_wakeup, S:01, do_nanosleep, sleep/1234, active

cpu: 1
 .expires_next   :   12345 nsecs
 .hres_active    : 1
 .tick_stopped   : 1
 #0: <0xffff>, hrtimer_wakeup, S:01, do_nanosleep, sleep/1234, active

Tick Device: mode:     1
Broadcast device
Clock Event Device: lapic-deadline
"""

SAMPLE_NOHZ_OFF = """\
Timer List Version: v0.9
now at 1234567890 nsecs

cpu: 0
 .hres_active    : 1
 .tick_stopped   : 0
 #0: <0xffff>, hrtimer_wakeup, S:01, do_nanosleep, sleep/1234, active

cpu: 1
 .tick_stopped   : 0
 #0: <0xffff>, hrtimer_wakeup, S:01, do_nanosleep, sleep/1234, active

Broadcast device
"""

SAMPLE_NO_BROADCAST = """\
Timer List Version: v0.9

cpu: 0
 .tick_stopped   : 1
 #0: <0xffff>, hrtimer_wakeup, S:01, do_nanosleep, sleep/1234, active
"""


# --- read_timer_list -------------------------------------------

def test_read_timer_list_missing(tmp_path):
    out = mod.read_timer_list(str(tmp_path / "nope"))
    assert out == {"present": False, "eacces": False,
                      "active_hrtimers": 0,
                      "broadcast_device_seen": False,
                      "tick_stopped_zero_count": 0,
                      "cpus_seen": 0}


def test_read_timer_list_healthy(tmp_path):
    p = tmp_path / "timer_list"
    _write_timer_list(str(p), SAMPLE_HEALTHY)
    out = mod.read_timer_list(str(p))
    assert out["present"] is True
    assert out["eacces"] is False
    assert out["active_hrtimers"] == 3
    assert out["broadcast_device_seen"] is True
    assert out["tick_stopped_zero_count"] == 0
    assert out["cpus_seen"] == 2


def test_read_timer_list_nohz_off(tmp_path):
    p = tmp_path / "timer_list"
    _write_timer_list(str(p), SAMPLE_NOHZ_OFF)
    out = mod.read_timer_list(str(p))
    assert out["tick_stopped_zero_count"] == 2
    assert out["broadcast_device_seen"] is True


def test_read_timer_list_no_broadcast(tmp_path):
    p = tmp_path / "timer_list"
    _write_timer_list(str(p), SAMPLE_NO_BROADCAST)
    out = mod.read_timer_list(str(p))
    assert out["broadcast_device_seen"] is False
    assert out["cpus_seen"] == 1


# --- read_clocksource ------------------------------------------

def test_read_clocksource_missing(tmp_path):
    out = mod.read_clocksource(str(tmp_path / "nope"))
    assert out == {"current": None, "available": []}


def test_read_clocksource(tmp_path):
    d = tmp_path / "cs"; d.mkdir()
    (d / "current_clocksource").write_text("tsc\n")
    (d / "available_clocksource").write_text("tsc hpet acpi_pm\n")
    out = mod.read_clocksource(str(d))
    assert out["current"] == "tsc"
    assert out["available"] == ["tsc", "hpet", "acpi_pm"]


# --- classify ---------------------------------------------------

def test_classify_unknown():
    v = mod.classify({"present": False, "eacces": False,
                          "active_hrtimers": 0,
                          "broadcast_device_seen": False,
                          "tick_stopped_zero_count": 0,
                          "cpus_seen": 0},
                          {"present": False, "eacces": False},
                          {"current": None, "available": []},
                          False)
    assert v["verdict"] == "unknown"


def test_classify_requires_root():
    v = mod.classify({"present": True, "eacces": True,
                          "active_hrtimers": 0,
                          "broadcast_device_seen": False,
                          "tick_stopped_zero_count": 0,
                          "cpus_seen": 0},
                          {"present": False, "eacces": False},
                          {"current": "tsc", "available": ["tsc"]},
                          True)
    assert v["verdict"] == "requires_root"


def test_classify_nohz_off():
    v = mod.classify({"present": True, "eacces": False,
                          "active_hrtimers": 5,
                          "broadcast_device_seen": True,
                          "tick_stopped_zero_count": 2,
                          "cpus_seen": 4},
                          {"present": False, "eacces": False},
                          {"current": "tsc", "available": ["tsc"]},
                          True)
    assert v["verdict"] == "nohz_disabled_on_idle_cpu"


def test_classify_broadcast_missing():
    v = mod.classify({"present": True, "eacces": False,
                          "active_hrtimers": 5,
                          "broadcast_device_seen": False,
                          "tick_stopped_zero_count": 0,
                          "cpus_seen": 4},
                          {"present": False, "eacces": False},
                          {"current": "tsc", "available": ["tsc"]},
                          True)
    assert v["verdict"] == "broadcast_device_missing"


def test_classify_hrtimer_runaway():
    v = mod.classify({"present": True, "eacces": False,
                          "active_hrtimers": 50_000,
                          "broadcast_device_seen": True,
                          "tick_stopped_zero_count": 0,
                          "cpus_seen": 4},
                          {"present": False, "eacces": False},
                          {"current": "tsc", "available": ["tsc"]},
                          True)
    assert v["verdict"] == "hrtimer_runaway"


def test_classify_ok():
    v = mod.classify({"present": True, "eacces": False,
                          "active_hrtimers": 100,
                          "broadcast_device_seen": True,
                          "tick_stopped_zero_count": 0,
                          "cpus_seen": 4},
                          {"present": False, "eacces": False},
                          {"current": "kvm-clock",
                            "available": ["kvm-clock"]},
                          True)
    assert v["verdict"] == "ok"


# Priority : nohz > broadcast > hrtimer
def test_priority_nohz_over_broadcast():
    v = mod.classify({"present": True, "eacces": False,
                          "active_hrtimers": 100,
                          "broadcast_device_seen": False,
                          "tick_stopped_zero_count": 1,
                          "cpus_seen": 4},
                          {"present": False, "eacces": False},
                          {"current": "tsc", "available": ["tsc"]},
                          True)
    assert v["verdict"] == "nohz_disabled_on_idle_cpu"


# --- status integration -----------------------------------------

def test_status_unknown(tmp_path):
    out = mod.status(None,
                          str(tmp_path / "no_tl"),
                          str(tmp_path / "no_ts"),
                          str(tmp_path / "no_cs"))
    assert out["ok"] is False
    assert out["verdict"]["verdict"] == "unknown"


def test_status_ok_synthetic(tmp_path):
    tl = tmp_path / "timer_list"
    _write_timer_list(str(tl), SAMPLE_HEALTHY)
    cs = tmp_path / "cs"; cs.mkdir()
    (cs / "current_clocksource").write_text("kvm-clock\n")
    (cs / "available_clocksource").write_text("kvm-clock\n")
    out = mod.status(None, str(tl),
                          str(tmp_path / "no_ts"), str(cs))
    assert out["ok"] is True
    assert out["timer_list_present"] is True
    assert out["clocksource_current"] == "kvm-clock"
    assert out["verdict"]["verdict"] == "ok"


def test_status_live_smoke():
    out = mod.status(None)
    assert out["verdict"]["verdict"] in (
        "ok", "nohz_disabled_on_idle_cpu",
        "broadcast_device_missing", "hrtimer_runaway",
        "requires_root", "unknown")
