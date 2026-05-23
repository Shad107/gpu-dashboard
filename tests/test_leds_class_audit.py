"""Tests for modules/leds_class_audit.py — R&D #63.3."""
from __future__ import annotations

import pytest

from gpu_dashboard.modules import leds_class_audit as mod


def _mk_led(root, name, *, trigger="none [kbd-capslock] timer",
              brightness=0, max_brightness=1):
    d = root / name
    d.mkdir(parents=True, exist_ok=True)
    (d / "trigger").write_text(trigger + "\n")
    (d / "brightness").write_text(f"{brightness}\n")
    (d / "max_brightness").write_text(f"{max_brightness}\n")


# --- _active_trigger --------------------------------------------

def test_active_trigger_yes():
    assert mod._active_trigger(
        "none [kbd-capslock] timer") == "kbd-capslock"


def test_active_trigger_no_bracket():
    assert mod._active_trigger("none kbd-capslock") is None


def test_active_trigger_empty():
    assert mod._active_trigger("") is None
    assert mod._active_trigger(None) is None


# --- list_leds --------------------------------------------------

def test_list_leds_missing(tmp_path):
    assert mod.list_leds(str(tmp_path / "nope")) == []


def test_list_leds(tmp_path):
    _mk_led(tmp_path, "input1::capslock")
    _mk_led(tmp_path, "input1::numlock")
    out = mod.list_leds(str(tmp_path))
    assert len(out) == 2


# --- classify ---------------------------------------------------

def _led(id_="input1::capslock", trigger_raw="none",
          active_trigger="none", brightness=0,
          max_brightness=1):
    return {"id": id_, "trigger_raw": trigger_raw,
              "active_trigger": active_trigger,
              "brightness": brightness,
              "max_brightness": max_brightness}


def test_classify_unknown():
    v = mod.classify([])
    assert v["verdict"] == "unknown"


def test_classify_ok():
    v = mod.classify([_led()])
    assert v["verdict"] == "ok"


def test_classify_stuck_on():
    v = mod.classify(
        [_led(brightness=1, max_brightness=1,
                active_trigger="none")])
    assert v["verdict"] == "led_stuck_on"


def test_classify_no_stuck_with_trigger():
    # Even if brightness=max, if trigger is not 'none', it's
    # actively used.
    v = mod.classify(
        [_led(brightness=1, max_brightness=1,
                active_trigger="kbd-capslock")])
    assert v["verdict"] == "ok"


def test_classify_flap():
    v = mod.classify([_led(active_trigger="timer")])
    assert v["verdict"] == "led_flap"


def test_classify_orphan():
    v = mod.classify(
        [_led(trigger_raw=None, brightness=None,
                active_trigger=None, max_brightness=None)])
    assert v["verdict"] == "led_orphan"


def test_classify_priority_stuck_wins():
    v = mod.classify(
        [_led(brightness=1, max_brightness=1,
                active_trigger="none"),
         _led(id_="other", active_trigger="timer")])
    assert v["verdict"] == "led_stuck_on"


# --- status integration -----------------------------------------

def test_status_unknown(tmp_path):
    out = mod.status(None, str(tmp_path / "nope"))
    assert out["ok"] is False
    assert out["verdict"]["verdict"] == "unknown"


def test_status_live_like(tmp_path):
    _mk_led(tmp_path, "input1::capslock",
              trigger="none [kbd-capslock]")
    _mk_led(tmp_path, "input1::numlock",
              trigger="none [kbd-numlock]")
    out = mod.status(None, str(tmp_path))
    assert out["ok"] is True
    assert out["led_count"] == 2
    assert out["verdict"]["verdict"] == "ok"
