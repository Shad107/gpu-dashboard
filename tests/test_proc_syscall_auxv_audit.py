"""Tests for modules/proc_syscall_auxv_audit.py — R&D #66.3."""
from __future__ import annotations

import os
import struct

import pytest

from gpu_dashboard.modules import proc_syscall_auxv_audit as mod


# --- parse_auxv -------------------------------------------------

def test_parse_auxv_empty():
    assert mod.parse_auxv(b"") == {}
    assert mod.parse_auxv(None) == {}


def test_parse_auxv_basic():
    # Two entries plus terminator.
    blob = struct.pack("<QQ", mod._AT_HWCAP, 0xdeadbeef)
    blob += struct.pack("<QQ", mod._AT_PAGESZ, 4096)
    blob += struct.pack("<QQ", 0, 0)  # terminator
    out = mod.parse_auxv(blob)
    assert out[mod._AT_HWCAP] == 0xdeadbeef
    assert out[mod._AT_PAGESZ] == 4096


def test_parse_auxv_handles_trailing_partial():
    blob = struct.pack("<QQ", mod._AT_HWCAP, 1)
    blob += b"abc"  # partial garbage
    out = mod.parse_auxv(blob)
    assert out == {mod._AT_HWCAP: 1}


def test_parse_auxv_extracts_secure_random_base():
    blob = struct.pack("<QQ", mod._AT_HWCAP, 1)
    blob += struct.pack("<QQ", mod._AT_SECURE, 1)
    blob += struct.pack("<QQ", mod._AT_RANDOM, 0x7fffaabb0000)
    blob += struct.pack("<QQ", mod._AT_BASE, 0x7f0000000000)
    blob += struct.pack("<QQ", 0, 0)
    out = mod.parse_auxv(blob)
    assert out[mod._AT_SECURE] == 1
    assert out[mod._AT_RANDOM] == 0x7fffaabb0000
    assert out[mod._AT_BASE] == 0x7f0000000000


# --- read_state -------------------------------------------------

def _mk_proc(root, pid, *, state="R", wchan="0",
                  timerslack=50000, syscall_line="-1 0x0 0x0"):
    d = root / str(pid)
    d.mkdir(parents=True, exist_ok=True)
    # /proc/<pid>/stat — minimal compatible layout
    (d / "stat").write_text(
        f"{pid} (cmd) {state} 0 0 0 0 0 0 0 0 0 0 0\n")
    (d / "wchan").write_text(wchan)
    (d / "timerslack_ns").write_text(f"{timerslack}\n")
    (d / "syscall").write_text(syscall_line + "\n")
    (d / "auxv").write_bytes(struct.pack("<QQ", mod._AT_HWCAP, 1))


def test_read_state(tmp_path):
    _mk_proc(tmp_path, 100, state="D")
    assert mod.read_state(100, str(tmp_path)) == "D"


def test_read_state_missing(tmp_path):
    assert mod.read_state(999, str(tmp_path)) is None


def test_read_wchan_zero(tmp_path):
    _mk_proc(tmp_path, 100, wchan="0")
    assert mod.read_wchan(100, str(tmp_path)) == "0"


def test_read_wchan_real(tmp_path):
    _mk_proc(tmp_path, 100, wchan="do_wait")
    assert mod.read_wchan(100, str(tmp_path)) == "do_wait"


def test_read_timerslack(tmp_path):
    _mk_proc(tmp_path, 100, timerslack=0)
    assert mod.read_timerslack(100, str(tmp_path)) == 0


def test_read_syscall(tmp_path):
    _mk_proc(tmp_path, 100, syscall_line="35 0x1 0x2")
    assert mod.read_syscall(100, str(tmp_path)) == "35"


def test_read_syscall_empty(tmp_path):
    _mk_proc(tmp_path, 100, syscall_line="")
    assert mod.read_syscall(100, str(tmp_path)) is None


# --- is_battery_discharging -------------------------------------

def test_battery_absent(tmp_path):
    assert mod.is_battery_discharging(str(tmp_path / "nope")) is False


def test_battery_charging(tmp_path):
    d = tmp_path / "BAT0"
    d.mkdir()
    (d / "status").write_text("Charging\n")
    assert mod.is_battery_discharging(str(tmp_path)) is False


def test_battery_discharging(tmp_path):
    d = tmp_path / "BAT0"
    d.mkdir()
    (d / "status").write_text("Discharging\n")
    assert mod.is_battery_discharging(str(tmp_path)) is True


def test_battery_skips_non_bat(tmp_path):
    d = tmp_path / "AC"
    d.mkdir()
    (d / "status").write_text("Discharging\n")
    assert mod.is_battery_discharging(str(tmp_path)) is False


# --- classify ---------------------------------------------------

def test_classify_unknown():
    v = mod.classify([], {}, "x86_64", False, False)
    assert v["verdict"] == "unknown"


def test_classify_ok():
    v = mod.classify(
        [{"pid": 1, "state": "S", "wchan": "0",
            "syscall": "-1", "timerslack_ns": 50000}],
        {mod._AT_HWCAP: 0x1f8bfbff}, "x86_64", False, True)
    assert v["verdict"] == "ok"


def test_classify_syscall_hang_long():
    v = mod.classify(
        [{"pid": 42, "state": "D", "wchan": "io_schedule",
            "syscall": "0", "timerslack_ns": 50000}],
        {mod._AT_HWCAP: 0x1f8bfbff}, "x86_64", False, True)
    assert v["verdict"] == "syscall_hang_long"
    assert "io_schedule" in v["reason"]


def test_classify_skips_D_with_zero_wchan():
    # Daemon that's just blocked in idle (wchan==0) — not a hang.
    v = mod.classify(
        [{"pid": 7, "state": "D", "wchan": "0",
            "syscall": "0", "timerslack_ns": 50000}],
        {mod._AT_HWCAP: 0x1f8bfbff}, "x86_64", False, True)
    assert v["verdict"] == "ok"


def test_classify_hwcap_drift():
    v = mod.classify(
        [{"pid": 1, "state": "S", "wchan": "0",
            "syscall": "-1", "timerslack_ns": 50000}],
        {mod._AT_HWCAP: 0}, "x86_64", False, True)
    assert v["verdict"] == "hwcap_drift"


def test_classify_timerslack_battery():
    v = mod.classify(
        [{"pid": 1, "state": "S", "wchan": "0",
            "syscall": "-1", "timerslack_ns": 0},
         {"pid": 2, "state": "S", "wchan": "0",
            "syscall": "-1", "timerslack_ns": 50000}],
        {mod._AT_HWCAP: 1}, "x86_64", True, True)
    assert v["verdict"] == "timerslack_battery_hostile"


def test_classify_timerslack_not_when_no_battery():
    # Same setup but battery NOT discharging → ok (timerslack only
    # matters when on battery).
    v = mod.classify(
        [{"pid": 1, "state": "S", "wchan": "0",
            "syscall": "-1", "timerslack_ns": 0}],
        {mod._AT_HWCAP: 1}, "x86_64", False, True)
    assert v["verdict"] == "ok"


# Priority: hang beats hwcap_drift beats timerslack.
def test_priority_hang_over_hwcap():
    v = mod.classify(
        [{"pid": 1, "state": "D", "wchan": "io_schedule",
            "syscall": "0", "timerslack_ns": 0}],
        {mod._AT_HWCAP: 0}, "x86_64", True, True)
    assert v["verdict"] == "syscall_hang_long"


def test_priority_hwcap_over_timerslack():
    v = mod.classify(
        [{"pid": 1, "state": "S", "wchan": "0",
            "syscall": "-1", "timerslack_ns": 0}],
        {mod._AT_HWCAP: 0}, "x86_64", True, True)
    assert v["verdict"] == "hwcap_drift"


def test_classify_unexpected_secure_mode():
    v = mod.classify(
        [{"pid": 1, "state": "S", "wchan": "0",
            "syscall": "-1", "timerslack_ns": 50000}],
        {mod._AT_HWCAP: 1, mod._AT_SECURE: 1},
        "x86_64", False, True)
    assert v["verdict"] == "unexpected_secure_mode"
    assert "AT_SECURE=1" in v["reason"]


def test_classify_secure_zero_is_ok():
    v = mod.classify(
        [{"pid": 1, "state": "S", "wchan": "0",
            "syscall": "-1", "timerslack_ns": 50000}],
        {mod._AT_HWCAP: 1, mod._AT_SECURE: 0},
        "x86_64", False, True)
    assert v["verdict"] == "ok"


def test_classify_secure_below_timerslack_priority():
    # Battery discharging + timerslack=0 + AT_SECURE=1 → timerslack
    # wins (higher severity than informational accent).
    v = mod.classify(
        [{"pid": 1, "state": "S", "wchan": "0",
            "syscall": "-1", "timerslack_ns": 0}],
        {mod._AT_HWCAP: 1, mod._AT_SECURE: 1},
        "x86_64", True, True)
    assert v["verdict"] == "timerslack_battery_hostile"


# --- status integration -----------------------------------------

def test_status_smoke_live():
    """Real /proc — should produce *some* verdict without crash."""
    out = mod.status(None)
    assert out["sample_count"] > 0
    assert "verdict" in out
    # New fields exposed by R&D #111.1 deepening.
    assert "own_secure" in out
    assert "own_at_base" in out
    assert "own_at_random_set" in out
    # On a live workstation/VM, we expect ok unless something's
    # genuinely wrong.
    assert out["verdict"]["verdict"] in (
        "ok", "syscall_hang_long", "hwcap_drift",
        "timerslack_battery_hostile",
        "unexpected_secure_mode", "unknown")
