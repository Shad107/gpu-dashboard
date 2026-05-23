"""Module backlight_pwm_audit — backlight + PWM chip (R&D #57.3).

Reads :
  /sys/class/backlight/<name>/{brightness, max_brightness,
                                  bl_power, actual_brightness, type}
  /sys/class/pwm/pwmchip*/{npwm, pwm*/{enable, period, duty_cycle}}

Why this matters on a homelab LLM host :

* A headless laptop / SFF hosting llama-server sometimes drops
  to `bl_power = 4` (FB_BLANK_POWERDOWN) or `brightness = 0`
  after a runtime-PM glitch on the GPU. The box is "alive but
  dark" — you can't read POST or the console during a hung CUDA
  driver reload.
* PWM channels left at duty=0 with enable=1 are a half-configured
  fan-control attempt that silently does nothing.

Verdicts (priority-ordered) :
  panel_blanked            ≥1 backlight has bl_power = 4
                           (FB_BLANK_POWERDOWN) — panel off.
  backlight_zero           ≥1 backlight has brightness = 0 AND
                           max_brightness > 0 — output present
                           but invisible.
  pwm_runaway              ≥1 PWM channel has enable=1 AND
                           duty_cycle = 0 — half-configured.
  ok                       backlights healthy, PWM channels
                           consistent, OR no backlight present.
  unknown                  /sys/class/backlight AND /sys/class/pwm
                           both absent.

stdlib only.
"""
from __future__ import annotations

import os
import re
from typing import List, Optional


NAME = "backlight_pwm_audit"


_SYS_BACKLIGHT = "/sys/class/backlight"
_SYS_PWM = "/sys/class/pwm"

_PWM_CHANNEL_RE = re.compile(r"^pwm\d+$")


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


def list_backlights(sys_backlight: str = _SYS_BACKLIGHT
                       ) -> List[dict]:
    if not os.path.isdir(sys_backlight):
        return []
    out: List[dict] = []
    for name in sorted(os.listdir(sys_backlight)):
        d = os.path.join(sys_backlight, name)
        out.append({
            "name": name,
            "brightness": _read_int(
                os.path.join(d, "brightness")),
            "max_brightness": _read_int(
                os.path.join(d, "max_brightness")),
            "bl_power": _read_int(
                os.path.join(d, "bl_power")),
            "actual_brightness": _read_int(
                os.path.join(d, "actual_brightness")),
            "type": _read(os.path.join(d, "type")),
        })
    return out


def list_pwm_chips(sys_pwm: str = _SYS_PWM) -> List[dict]:
    if not os.path.isdir(sys_pwm):
        return []
    out: List[dict] = []
    for chip_name in sorted(os.listdir(sys_pwm)):
        if not chip_name.startswith("pwmchip"):
            continue
        chip_dir = os.path.join(sys_pwm, chip_name)
        chip = {
            "name": chip_name,
            "npwm": _read_int(os.path.join(chip_dir, "npwm")),
            "channels": [],
        }
        if os.path.isdir(chip_dir):
            for ch_name in sorted(os.listdir(chip_dir)):
                if not _PWM_CHANNEL_RE.match(ch_name):
                    continue
                ch_dir = os.path.join(chip_dir, ch_name)
                chip["channels"].append({
                    "name": ch_name,
                    "enable": _read_int(
                        os.path.join(ch_dir, "enable")),
                    "period": _read_int(
                        os.path.join(ch_dir, "period")),
                    "duty_cycle": _read_int(
                        os.path.join(ch_dir, "duty_cycle")),
                })
        out.append(chip)
    return out


def classify(backlights: List[dict], pwm_chips: List[dict]) -> dict:
    if not backlights and not pwm_chips:
        return {"verdict": "unknown",
                "reason": ("/sys/class/backlight and /sys/class/pwm "
                          "both absent — server class host."),
                "recommendation": ""}

    # 1) panel_blanked — bl_power = 4 means FB_BLANK_POWERDOWN
    blanked = [b for b in backlights
                  if b.get("bl_power") == 4]
    if blanked:
        sample = ", ".join(b["name"] for b in blanked[:3])
        return {"verdict": "panel_blanked",
                "reason": (f"{len(blanked)} backlight(s) report "
                          f"bl_power = 4 (FB_BLANK_POWERDOWN) : "
                          f"{sample}. Panel off — you can't read "
                          f"POST / console."),
                "recommendation": _recipe_unblank()}

    # 2) backlight_zero — brightness = 0 with non-zero max
    dark = [b for b in backlights
              if (b.get("brightness") == 0 and
                  (b.get("max_brightness") or 0) > 0)]
    if dark:
        sample = ", ".join(b["name"] for b in dark[:3])
        return {"verdict": "backlight_zero",
                "reason": (f"{len(dark)} backlight(s) at "
                          f"brightness 0 / max > 0 : {sample}. "
                          f"Output present but invisible."),
                "recommendation": _recipe_raise_brightness()}

    # 3) pwm_runaway — enable=1 with duty=0 across any chip
    runaway = []
    for chip in pwm_chips:
        for ch in chip.get("channels", []):
            if (ch.get("enable") == 1 and
                    ch.get("duty_cycle") == 0):
                runaway.append(f"{chip['name']}/{ch['name']}")
    if runaway:
        sample = ", ".join(runaway[:3])
        return {"verdict": "pwm_runaway",
                "reason": (f"{len(runaway)} PWM channel(s) enabled "
                          f"with duty_cycle = 0 : {sample}. Half-"
                          f"configured fan / LED control."),
                "recommendation": _recipe_pwm_set()}

    return {"verdict": "ok",
            "reason": (f"{len(backlights)} backlight(s), "
                      f"{len(pwm_chips)} PWM chip(s) — all "
                      f"consistent."),
            "recommendation": ""}


def status(config=None,
            sys_backlight: str = _SYS_BACKLIGHT,
            sys_pwm: str = _SYS_PWM) -> dict:
    backlights = list_backlights(sys_backlight)
    pwm_chips = list_pwm_chips(sys_pwm)
    ok = bool(backlights or pwm_chips)
    verdict = classify(backlights, pwm_chips)
    return {"ok": ok,
              "backlight_count": len(backlights),
              "backlights": backlights,
              "pwm_chip_count": len(pwm_chips),
              "pwm_chips": pwm_chips,
              "verdict": verdict}


# ── recovery recipes ────────────────────────────────────────────

def _recipe_unblank() -> str:
    return ("# Bring the panel back online :\n"
            "echo 0 | sudo tee /sys/class/backlight/*/bl_power\n"
            "# … and set a visible brightness :\n"
            "for b in /sys/class/backlight/*; do\n"
            "  m=$(cat $b/max_brightness)\n"
            "  echo $((m / 2)) | sudo tee $b/brightness\n"
            "done\n")


def _recipe_raise_brightness() -> str:
    return ("# Restore visible brightness :\n"
            "for b in /sys/class/backlight/*; do\n"
            "  m=$(cat $b/max_brightness)\n"
            "  echo $((m * 4 / 5)) | sudo tee $b/brightness\n"
            "done\n"
            "# Persist via /etc/systemd/backlight@<name>.service\n")


def _recipe_pwm_set() -> str:
    return ("# Either disable the channel or set a meaningful duty :\n"
            "for c in /sys/class/pwm/pwmchip*/pwm*; do\n"
            "  if [ \"$(cat $c/enable 2>/dev/null)\" = \"1\" ] && \\\n"
            "     [ \"$(cat $c/duty_cycle 2>/dev/null)\" = \"0\" ]; then\n"
            "    echo \"$c : disabling\"\n"
            "    echo 0 | sudo tee $c/enable\n"
            "  fi\n"
            "done\n")
