"""Tests for modules/backlight_pwm_audit.py — R&D #57.3."""
from __future__ import annotations

import pytest

from gpu_dashboard.modules import backlight_pwm_audit as mod


def _mk_backlight(root, name, *, brightness=512, max_brightness=1024,
                    bl_power=0, type_="raw"):
    d = root / name
    d.mkdir(parents=True, exist_ok=True)
    (d / "brightness").write_text(f"{brightness}\n")
    (d / "max_brightness").write_text(f"{max_brightness}\n")
    (d / "bl_power").write_text(f"{bl_power}\n")
    (d / "actual_brightness").write_text(f"{brightness}\n")
    (d / "type").write_text(type_ + "\n")


def _mk_pwm_chip(root, chip_idx, channels=None):
    d = root / f"pwmchip{chip_idx}"
    d.mkdir(parents=True, exist_ok=True)
    (d / "npwm").write_text(f"{len(channels or [])}\n")
    for i, ch in enumerate(channels or []):
        ch_d = d / f"pwm{i}"
        ch_d.mkdir(parents=True, exist_ok=True)
        (ch_d / "enable").write_text(f"{ch.get('enable', 0)}\n")
        (ch_d / "period").write_text(f"{ch.get('period', 0)}\n")
        (ch_d / "duty_cycle").write_text(
            f"{ch.get('duty_cycle', 0)}\n")


# --- list_backlights --------------------------------------------

def test_list_backlights_missing(tmp_path):
    assert mod.list_backlights(str(tmp_path / "nope")) == []


def test_list_backlights(tmp_path):
    _mk_backlight(tmp_path, "intel_backlight",
                     brightness=800, max_brightness=1000)
    out = mod.list_backlights(str(tmp_path))
    assert len(out) == 1
    assert out[0]["brightness"] == 800
    assert out[0]["max_brightness"] == 1000


# --- list_pwm_chips ---------------------------------------------

def test_list_pwm_chips_missing(tmp_path):
    assert mod.list_pwm_chips(str(tmp_path / "nope")) == []


def test_list_pwm_chips(tmp_path):
    _mk_pwm_chip(tmp_path, 0,
                    channels=[{"enable": 1, "period": 1000,
                                  "duty_cycle": 500}])
    out = mod.list_pwm_chips(str(tmp_path))
    assert len(out) == 1
    assert out[0]["npwm"] == 1
    assert out[0]["channels"][0]["enable"] == 1


# --- classify ---------------------------------------------------

def _bl(name="intel_backlight", brightness=512, max_brightness=1024,
         bl_power=0):
    return {"name": name, "brightness": brightness,
              "max_brightness": max_brightness, "bl_power": bl_power,
              "actual_brightness": brightness, "type": "raw"}


def _pwm_chip(name="pwmchip0", channels=None):
    return {"name": name, "npwm": len(channels or []),
              "channels": channels or []}


def _pwm_ch(name="pwm0", enable=1, period=1000, duty_cycle=500):
    return {"name": name, "enable": enable, "period": period,
              "duty_cycle": duty_cycle}


def test_classify_unknown():
    v = mod.classify([], [])
    assert v["verdict"] == "unknown"


def test_classify_ok_just_pwm():
    # PWM present but no backlights → still ok
    v = mod.classify([], [_pwm_chip(channels=[_pwm_ch()])])
    assert v["verdict"] == "ok"


def test_classify_ok_just_backlights():
    v = mod.classify([_bl()], [])
    assert v["verdict"] == "ok"


def test_classify_panel_blanked():
    v = mod.classify([_bl(bl_power=4)], [])
    assert v["verdict"] == "panel_blanked"


def test_classify_backlight_zero():
    v = mod.classify([_bl(brightness=0)], [])
    assert v["verdict"] == "backlight_zero"


def test_classify_pwm_runaway():
    v = mod.classify(
        [_bl()],
        [_pwm_chip(channels=[_pwm_ch(enable=1,
                                            duty_cycle=0)])])
    assert v["verdict"] == "pwm_runaway"


def test_classify_priority_blanked_wins():
    v = mod.classify(
        [_bl(bl_power=4, brightness=0)],
        [_pwm_chip(channels=[_pwm_ch(enable=1,
                                            duty_cycle=0)])])
    assert v["verdict"] == "panel_blanked"


# --- status integration -----------------------------------------

def test_status_unknown(tmp_path):
    out = mod.status(None, str(tmp_path / "nope1"),
                       str(tmp_path / "nope2"))
    assert out["ok"] is False
    assert out["verdict"]["verdict"] == "unknown"


def test_status_live_like(tmp_path):
    bl = tmp_path / "backlight"
    _mk_backlight(bl, "intel_backlight",
                     brightness=800, max_brightness=1000)
    pw = tmp_path / "pwm"
    _mk_pwm_chip(pw, 0,
                    channels=[{"enable": 1, "period": 1000,
                                  "duty_cycle": 500}])
    out = mod.status(None, str(bl), str(pw))
    assert out["ok"] is True
    assert out["backlight_count"] == 1
    assert out["pwm_chip_count"] == 1
    assert out["verdict"]["verdict"] == "ok"
