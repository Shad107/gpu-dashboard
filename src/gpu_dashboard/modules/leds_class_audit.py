"""Module leds_class_audit — /sys/class/leds (R&D #63.3).

Reads /sys/class/leds/*/{trigger, brightness, max_brightness}.

Why this matters on a laptop / SFF LLM rig :

* A vendor LED (platform::power, chassis::status) left on the
  `timer` trigger by an old shell script drains 30-50 mA
  continuously on suspend-to-idle laptops.
* A status LED stuck at max_brightness via the `none` trigger is
  the classic "forgot to turn it off" pattern.

Distinct from R&D #57.3 backlight_pwm_audit (panel backlight only —
the /sys/class/backlight subtree). /sys/class/leds covers chassis +
keyboard + WiFi-status indicator LEDs.

Verdicts (priority-ordered) :
  led_stuck_on          ≥1 LED at brightness = max_brightness
                        AND trigger = `none` (forgotten on).
  led_flap              ≥1 LED on `timer` trigger (constant
                        flash drains battery).
  led_orphan            ≥1 LED with neither trigger nor
                        brightness readable (sysfs node stale).
  ok                    LEDs healthy or off.
  unknown               /sys/class/leds absent.

stdlib only.
"""
from __future__ import annotations

import os
import re
from typing import List, Optional


NAME = "leds_class_audit"


_SYS_LEDS = "/sys/class/leds"


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


def _active_trigger(raw: Optional[str]) -> Optional[str]:
    """Trigger file looks like 'none [kbd-capslock] usb-host' —
    the bracketed token is the active one."""
    if not raw:
        return None
    for tok in raw.split():
        if tok.startswith("[") and tok.endswith("]"):
            return tok[1:-1]
    return None


def list_leds(sys_leds: str = _SYS_LEDS) -> List[dict]:
    if not os.path.isdir(sys_leds):
        return []
    out: List[dict] = []
    for name in sorted(os.listdir(sys_leds)):
        d = os.path.join(sys_leds, name)
        if not os.path.isdir(d):
            continue
        trigger_raw = _read(os.path.join(d, "trigger"))
        out.append({
            "id": name,
            "trigger_raw": trigger_raw,
            "active_trigger": _active_trigger(trigger_raw),
            "brightness": _read_int(
                os.path.join(d, "brightness")),
            "max_brightness": _read_int(
                os.path.join(d, "max_brightness")),
        })
    return out


def classify(leds: List[dict]) -> dict:
    if not leds:
        return {"verdict": "unknown",
                "reason": "/sys/class/leds absent or empty.",
                "recommendation": ""}

    # 1) led_stuck_on — brightness = max AND trigger = none
    stuck = [l for l in leds
                if (l.get("brightness") is not None and
                    l.get("max_brightness") is not None and
                    l["brightness"] > 0 and
                    l["brightness"] == l["max_brightness"] and
                    l.get("active_trigger") in (None, "none"))]
    if stuck:
        sample = ", ".join(l["id"] for l in stuck[:3])
        return {"verdict": "led_stuck_on",
                "reason": (f"{len(stuck)} LED(s) stuck at max "
                          f"brightness with no trigger : {sample}. "
                          f"Probably forgotten on by a script."),
                "recommendation": _recipe_led_off()}

    # 2) led_flap — timer trigger active
    timers = [l for l in leds
                 if l.get("active_trigger") == "timer"]
    if timers:
        sample = ", ".join(l["id"] for l in timers[:3])
        return {"verdict": "led_flap",
                "reason": (f"{len(timers)} LED(s) on 'timer' "
                          f"trigger : {sample}. Constant flash "
                          f"drains battery."),
                "recommendation": _recipe_led_off()}

    # 3) led_orphan — both trigger AND brightness unreadable
    orphans = [l for l in leds
                  if l.get("trigger_raw") is None and
                     l.get("brightness") is None]
    if orphans:
        sample = ", ".join(l["id"] for l in orphans[:3])
        return {"verdict": "led_orphan",
                "reason": (f"{len(orphans)} LED sysfs node(s) "
                          f"with unreadable trigger AND "
                          f"brightness : {sample}. Stale entry "
                          f"after a driver unbind."),
                "recommendation": _recipe_led_orphan()}

    return {"verdict": "ok",
            "reason": (f"{len(leds)} LED(s) — none drained, no "
                      f"flap timers, no orphans."),
            "recommendation": ""}


def status(config=None, sys_leds: str = _SYS_LEDS) -> dict:
    leds = list_leds(sys_leds)
    ok = bool(leds)
    verdict = classify(leds)
    return {"ok": ok,
              "led_count": len(leds),
              "leds": leds,
              "verdict": verdict}


# ── recovery recipes ────────────────────────────────────────────

def _recipe_led_off() -> str:
    return ("# Turn the LED(s) off and clear any timer trigger :\n"
            "for d in /sys/class/leds/*; do\n"
            "  if [ \"$(cat $d/brightness 2>/dev/null)\" != 0 ]; then\n"
            "    echo none | sudo tee $d/trigger\n"
            "    echo 0 | sudo tee $d/brightness\n"
            "  fi\n"
            "done\n"
            "# Persist via /etc/tmpfiles.d/ for boot-time defaults.\n")


def _recipe_led_orphan() -> str:
    return ("# Inspect the orphan node — usually a driver unbound\n"
            "# without cleanup :\n"
            "find /sys/class/leds -maxdepth 2 -type l\n"
            "lsmod | grep -i led\n"
            "# A reboot or driver reload usually clears them.\n")
