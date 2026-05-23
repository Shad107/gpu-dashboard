"""Tests for modules/hwmon_sensors_audit.py — R&D #55.3."""
from __future__ import annotations

import pytest

from gpu_dashboard.modules import hwmon_sensors_audit as mod


def _mk_chip(root, name, idx, *, fans=None, voltages=None,
              pwms=None):
    d = root / f"hwmon{idx}"
    d.mkdir(parents=True, exist_ok=True)
    (d / "name").write_text(name + "\n")
    for i, f in enumerate(fans or [], start=1):
        if "input" in f:
            (d / f"fan{i}_input").write_text(f"{f['input']}\n")
        if "alarm" in f:
            (d / f"fan{i}_alarm").write_text(f"{f['alarm']}\n")
    for i, v in enumerate(voltages or [], start=1):
        if "alarm" in v:
            (d / f"in{i}_alarm").write_text(f"{v['alarm']}\n")
    for i, p in enumerate(pwms or [], start=1):
        if "duty" in p:
            (d / f"pwm{i}").write_text(f"{p['duty']}\n")
        if "enable" in p:
            (d / f"pwm{i}_enable").write_text(f"{p['enable']}\n")
    return d


# --- list_chips -------------------------------------------------

def test_list_chips_missing(tmp_path):
    assert mod.list_chips(str(tmp_path / "nope")) == []


def test_list_chips_empty(tmp_path):
    assert mod.list_chips(str(tmp_path)) == []


def test_list_chips_basic(tmp_path):
    _mk_chip(tmp_path, "nct6775", 0,
                fans=[{"input": 1234, "alarm": 0}],
                voltages=[{"alarm": 0}],
                pwms=[{"duty": 128, "enable": 2}])
    out = mod.list_chips(str(tmp_path))
    assert len(out) == 1
    assert out[0]["name"] == "nct6775"
    assert out[0]["fans"][1]["input"] == 1234
    assert out[0]["pwms"][1]["enable"] == 2


# --- helper tests -----------------------------------------------

def _chip(name="nct6775", fans=None, voltages=None, pwms=None):
    return {"id": "hwmon0", "name": name,
              "fans": fans or {1: {"input": 1234, "alarm": 0}},
              "voltage_alarms": voltages or {1: {"alarm": 0}},
              "pwms": pwms or {1: {"duty": 128, "enable": 2}}}


def test_voltage_alarms():
    assert mod._voltage_alarms([_chip()]) == []
    assert mod._voltage_alarms(
        [_chip(voltages={1: {"alarm": 1}})]) == ["nct6775/in1"]


def test_fan_stalls():
    # Stall : fan=0 RPM while pwm > 0
    out = mod._fan_stalls(
        [_chip(fans={1: {"input": 0, "alarm": 0}},
                pwms={1: {"duty": 128, "enable": 2}})])
    assert out == ["nct6775/fan1"]


def test_fan_stalls_no_pwm():
    # Fan stopped intentionally (pwm=0) — not a stall
    out = mod._fan_stalls(
        [_chip(fans={1: {"input": 0}},
                pwms={1: {"duty": 0, "enable": 2}})])
    assert out == []


def test_manual_pwms():
    out = mod._manual_pwms(
        [_chip(pwms={1: {"duty": 128, "enable": 1}})])
    assert out == ["nct6775/pwm1"]


# --- classify ---------------------------------------------------

def test_classify_unknown():
    v = mod.classify([], hwmon_present=False)
    assert v["verdict"] == "unknown"


def test_classify_sensor_missing():
    v = mod.classify([], hwmon_present=True)
    assert v["verdict"] == "sensor_missing"


def test_classify_ok():
    v = mod.classify([_chip()], hwmon_present=True)
    assert v["verdict"] == "ok"


def test_classify_voltage_alarm():
    v = mod.classify([_chip(voltages={1: {"alarm": 1}})],
                       hwmon_present=True)
    assert v["verdict"] == "voltage_alarm"


def test_classify_fan_stall():
    v = mod.classify(
        [_chip(fans={1: {"input": 0}},
                pwms={1: {"duty": 200, "enable": 2}})],
        hwmon_present=True)
    assert v["verdict"] == "fan_stall"


def test_classify_pwm_manual():
    v = mod.classify(
        [_chip(pwms={1: {"duty": 200, "enable": 1}})],
        hwmon_present=True)
    assert v["verdict"] == "pwm_manual_override"


def test_classify_priority_voltage_wins():
    v = mod.classify(
        [_chip(voltages={1: {"alarm": 1}},
                fans={1: {"input": 0}},
                pwms={1: {"duty": 200, "enable": 1}})],
        hwmon_present=True)
    assert v["verdict"] == "voltage_alarm"


# --- status integration -----------------------------------------

def test_status_unknown(tmp_path):
    out = mod.status(None, str(tmp_path / "nope"))
    assert out["ok"] is False
    assert out["verdict"]["verdict"] == "unknown"


def test_status_empty(tmp_path):
    out = mod.status(None, str(tmp_path))
    assert out["ok"] is True
    assert out["verdict"]["verdict"] == "sensor_missing"


def test_status_with_chip(tmp_path):
    _mk_chip(tmp_path, "nct6775", 0,
                fans=[{"input": 0, "alarm": 0}],
                pwms=[{"duty": 180, "enable": 2}])
    out = mod.status(None, str(tmp_path))
    assert out["ok"] is True
    assert out["chip_count"] == 1
    assert out["verdict"]["verdict"] == "fan_stall"
