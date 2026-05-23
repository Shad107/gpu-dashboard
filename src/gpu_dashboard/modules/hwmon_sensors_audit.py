"""Module hwmon_sensors_audit — fans / voltages / PWM (R&D #55.3).

Distinct from existing /sys/class/thermal (zones + trip points only)
and RAPL (CPU package energy). /sys/class/hwmon/ exposes per-chip
sensor channels :

  fan*_input         tach RPM
  fan*_alarm         hardware threshold breach
  fan*_min           minimum RPM (stall threshold)
  in*_input          voltage rail (mV)
  in*_alarm          voltage alarm bit
  curr*_input        current rail (mA)
  pwm*               PWM duty (0-255)
  pwm*_enable        0 = full ; 1 = manual ; 2 = thermal-auto
  temp*_input        temperature (m°C)

Catches the bare-metal foot-guns next to a 3090 :

* Case / CPU fan reads 0 RPM under load → silently overheats
  the GPU sitting next door.
* VRM voltage alarm bit set on the motherboard's super-IO chip
  → CPU is browning out under inference spikes.
* pwm*_enable=1 (manual) left by a Windows-side tuning tool
  the user dual-booted out of → thermal control bypassed.

Reads :
  /sys/class/hwmon/hwmon*/{name, fan*_input, fan*_alarm,
                              fan*_min, in*_input, in*_alarm,
                              in*_min, in*_max, curr*_input,
                              pwm*, pwm*_enable, temp*_input}

Verdicts (priority-ordered) :
  voltage_alarm           in*_alarm = 1 on any rail.
  fan_stall               fan*_input = 0 on any chip while
                           pwm > 0 (driver expects rotation).
  pwm_manual_override     ≥1 pwm*_enable = 1 (manual mode).
  sensor_missing          /sys/class/hwmon empty (no driver) or
                           hwmon dir absent.
  ok                      readable, alarms clear, fans turning.
  unknown                 /sys/class/hwmon not present.

stdlib only.
"""
from __future__ import annotations

import os
import re
from typing import Dict, List, Optional


NAME = "hwmon_sensors_audit"


_SYS_HWMON = "/sys/class/hwmon"


_FAN_INPUT_RE = re.compile(r"^fan(\d+)_input$")
_FAN_ALARM_RE = re.compile(r"^fan(\d+)_alarm$")
_IN_ALARM_RE = re.compile(r"^in(\d+)_alarm$")
_PWM_RE = re.compile(r"^pwm(\d+)$")
_PWM_ENABLE_RE = re.compile(r"^pwm(\d+)_enable$")


def _read(p: str) -> Optional[str]:
    try:
        with open(p) as f:
            return f.read().strip()
    except OSError:
        return None


def _read_int(p: str) -> Optional[int]:
    t = _read(p)
    if t is None:
        return None
    try:
        return int(t)
    except ValueError:
        return None


def list_chips(sys_hwmon: str = _SYS_HWMON) -> List[dict]:
    """Walk /sys/class/hwmon/hwmon* and read all channels per chip."""
    if not os.path.isdir(sys_hwmon):
        return []
    out: List[dict] = []
    for name in sorted(os.listdir(sys_hwmon)):
        if not name.startswith("hwmon"):
            continue
        d = os.path.join(sys_hwmon, name)
        chip_name = _read(os.path.join(d, "name"))
        fans: Dict[int, dict] = {}
        ins: Dict[int, dict] = {}
        pwms: Dict[int, dict] = {}
        if not os.path.isdir(d):
            continue
        for fname in sorted(os.listdir(d)):
            m = _FAN_INPUT_RE.match(fname)
            if m:
                idx = int(m.group(1))
                fans.setdefault(idx, {})["input"] = _read_int(
                    os.path.join(d, fname))
                continue
            m = _FAN_ALARM_RE.match(fname)
            if m:
                idx = int(m.group(1))
                fans.setdefault(idx, {})["alarm"] = _read_int(
                    os.path.join(d, fname))
                continue
            m = _IN_ALARM_RE.match(fname)
            if m:
                idx = int(m.group(1))
                ins.setdefault(idx, {})["alarm"] = _read_int(
                    os.path.join(d, fname))
                continue
            m = _PWM_ENABLE_RE.match(fname)
            if m:
                idx = int(m.group(1))
                pwms.setdefault(idx, {})["enable"] = _read_int(
                    os.path.join(d, fname))
                continue
            m = _PWM_RE.match(fname)
            if m:
                idx = int(m.group(1))
                pwms.setdefault(idx, {})["duty"] = _read_int(
                    os.path.join(d, fname))
        out.append({
            "id": name,
            "name": chip_name,
            "fans": fans,
            "voltage_alarms": ins,
            "pwms": pwms,
        })
    return out


def _voltage_alarms(chips: List[dict]) -> List[str]:
    bad: List[str] = []
    for c in chips:
        for idx, ch in (c.get("voltage_alarms") or {}).items():
            if ch.get("alarm") == 1:
                bad.append(f"{c['name'] or c['id']}/in{idx}")
    return bad


def _fan_stalls(chips: List[dict]) -> List[str]:
    bad: List[str] = []
    for c in chips:
        # If any PWM on this chip is non-zero, fan should rotate.
        any_pwm_on = False
        for p in (c.get("pwms") or {}).values():
            duty = p.get("duty")
            if duty is not None and duty > 0:
                any_pwm_on = True
                break
        for idx, f in (c.get("fans") or {}).items():
            rpm = f.get("input")
            if rpm == 0 and any_pwm_on:
                bad.append(f"{c['name'] or c['id']}/fan{idx}")
    return bad


def _manual_pwms(chips: List[dict]) -> List[str]:
    bad: List[str] = []
    for c in chips:
        for idx, p in (c.get("pwms") or {}).items():
            if p.get("enable") == 1:
                bad.append(f"{c['name'] or c['id']}/pwm{idx}")
    return bad


def classify(chips: List[dict], hwmon_present: bool) -> dict:
    if not hwmon_present:
        return {"verdict": "unknown",
                "reason": ("/sys/class/hwmon is not present — "
                          "kernel built without hwmon or no chip "
                          "driver loaded."),
                "recommendation": ""}

    if not chips:
        return {"verdict": "sensor_missing",
                "reason": ("/sys/class/hwmon is empty — no sensor "
                          "driver loaded. Run sensors-detect to "
                          "find the right module."),
                "recommendation": _recipe_sensors_detect()}

    # 1) voltage_alarm
    vbad = _voltage_alarms(chips)
    if vbad:
        return {"verdict": "voltage_alarm",
                "reason": (f"Voltage alarm bit set on : "
                          f"{', '.join(vbad[:3])}."),
                "recommendation": _recipe_voltage_alarm()}

    # 2) fan_stall
    fbad = _fan_stalls(chips)
    if fbad:
        return {"verdict": "fan_stall",
                "reason": (f"Fan reads 0 RPM while PWM > 0 : "
                          f"{', '.join(fbad[:3])}. Stalled fan or "
                          f"missing tach."),
                "recommendation": _recipe_fan_stall()}

    # 3) pwm_manual_override
    mpwm = _manual_pwms(chips)
    if mpwm:
        return {"verdict": "pwm_manual_override",
                "reason": (f"{len(mpwm)} PWM channel(s) in manual "
                          f"mode (pwm*_enable=1) : "
                          f"{', '.join(mpwm[:3])}."),
                "recommendation": _recipe_pwm_auto()}

    return {"verdict": "ok",
            "reason": (f"{len(chips)} hwmon chip(s) ; alarms clear, "
                      f"fans turning when commanded."),
            "recommendation": ""}


def status(config=None, sys_hwmon: str = _SYS_HWMON) -> dict:
    hwmon_present = os.path.isdir(sys_hwmon)
    chips = list_chips(sys_hwmon)
    ok = hwmon_present
    verdict = classify(chips, hwmon_present)
    return {"ok": ok,
              "hwmon_present": hwmon_present,
              "chip_count": len(chips),
              "chips": chips,
              "verdict": verdict}


# ── recovery recipes ────────────────────────────────────────────

def _recipe_sensors_detect() -> str:
    return ("# Find the right hwmon driver for your motherboard /\n"
            "# super-IO chip :\n"
            "sudo apt install lm-sensors  # Debian/Ubuntu\n"
            "sudo sensors-detect --auto\n"
            "sudo systemctl restart lm-sensors\n"
            "# Then re-check : ls /sys/class/hwmon/\n")


def _recipe_voltage_alarm() -> str:
    return ("# A voltage rail crossed its alarm threshold — check\n"
            "# motherboard EC / PSU under load :\n"
            "sensors  # human-readable view\n"
            "grep -H . /sys/class/hwmon/hwmon*/in*_{input,alarm,max,min} 2>/dev/null\n"
            "# Common causes : overloaded PSU under GPU spike, weak\n"
            "# VRM caps on an aging board, BIOS undervolt left in.\n")


def _recipe_fan_stall() -> str:
    return ("# A fan reports 0 RPM while told to spin. Verify\n"
            "# physically that the fan is rotating ; if it is, the\n"
            "# tach wire is unplugged. If it isn't, replace the fan\n"
            "# before the GPU next door bakes :\n"
            "sensors\n"
            "for f in /sys/class/hwmon/hwmon*/fan*_input; do\n"
            "  echo \"$f : $(cat $f)\"\n"
            "done\n")


def _recipe_pwm_auto() -> str:
    return ("# Return PWM channels to thermal-auto so the SuperIO\n"
            "# chip controls the fan curve :\n"
            "for f in /sys/class/hwmon/hwmon*/pwm*_enable; do\n"
            "  echo 2 | sudo tee $f\n"
            "done\n"
            "# Persist via fancontrol / your fan-curve tool.\n")
