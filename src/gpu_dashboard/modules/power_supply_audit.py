"""Module power_supply_audit — /sys/class/power_supply (R&D #51.1).

Walks /sys/class/power_supply/* for battery / AC / UPS health.

Distinct from shipped ups_nut (NUT-protocol via TCP) and shipped
ups_runtime (NUT runtime estimation) — this reads the kernel
sysfs view directly, no daemon required.

Per-device fields :
  type                  Battery / Mains / UPS / USB / Unknown
  status                Charging / Discharging / Full / Not charging
  present               1 = present, 0 = absent
  capacity              0..100 %
  cycle_count           total charge cycles (battery health metric)
  charge_full           current full-charge capacity µAh
  charge_full_design    design full-charge capacity µAh
  charge_now            current charge µAh
  energy_full / energy_full_design / energy_now  (Wh variants)
  charge_control_start_threshold / charge_control_end_threshold
                        manufacturer-supported charge limits
  model_name + manufacturer
  scope                 System / Device (some HID UPSes)

Verdicts (priority-ordered) :
  battery_degraded         (charge_full / charge_full_design) < 60 %
                           on any battery → physical wear, near
                           end of life.
  no_ac                    AC mains absent OR offline AND ≥1
                           battery type → running on battery.
  charge_threshold_unset   Battery with charge_control_end_threshold
                           = 100 → no limit set, accelerates wear.
  ok                       battery health ≥ 60 %, AC online OR no
                           battery.
  no_power_supply          /sys/class/power_supply empty (typical
                           server / VM).
  unknown                  /sys/class/power_supply unreadable.

stdlib only.
"""
from __future__ import annotations

import os
from typing import Optional


NAME = "power_supply_audit"


_SYS_POWER = "/sys/class/power_supply"


def _read(p: str) -> Optional[str]:
    try:
        with open(p) as f:
            return f.read()
    except OSError:
        return None


def _read_int(p: str) -> Optional[int]:
    t = _read(p)
    if t is None:
        return None
    try:
        return int(t.strip())
    except ValueError:
        return None


def list_supplies(sys_power: str = _SYS_POWER) -> list:
    if not os.path.isdir(sys_power):
        return []
    out: list = []
    try:
        for name in sorted(os.listdir(sys_power)):
            d = os.path.join(sys_power, name)
            if not os.path.isdir(d):
                continue
            rec: dict = {"name": name}
            for attr in ("type", "status", "model_name",
                          "manufacturer", "scope"):
                v = _read(os.path.join(d, attr))
                if v is not None:
                    rec[attr] = v.strip() or None
            for attr in ("present", "online", "capacity",
                          "cycle_count", "charge_full",
                          "charge_full_design", "charge_now",
                          "energy_full", "energy_full_design",
                          "energy_now", "voltage_now",
                          "charge_control_start_threshold",
                          "charge_control_end_threshold"):
                v = _read_int(os.path.join(d, attr))
                if v is not None:
                    rec[attr] = v
            out.append(rec)
    except OSError:
        return []
    return out


_RECIPE_BATTERY_DEGRADED = (
    "# Battery is degraded (full charge < 60 % of design). Common\n"
    "# at 3+ years of use. Options :\n"
    "#  - Replace battery if laptop (most ThinkPad / Framework\n"
    "#    have user-replaceable batteries).\n"
    "#  - For dGPU-tower UPS battery : verify UPS APC / CyberPower\n"
    "#    runtime via shipped ups_nut module ; replace if < 5 min\n"
    "#    runtime under load."
)

_RECIPE_NO_AC = (
    "# AC mains is offline AND there's a battery — running on\n"
    "# battery. If unexpected, check :\n"
    "#  - PSU + outlet on tower rigs (a flaky GFCI can trip the\n"
    "#    UPS into battery mode silently).\n"
    "#  - UPS battery percent (shipped ups_runtime + ups_nut)."
)

_RECIPE_CHARGE_THRESHOLD = (
    "# Battery charge_control_end_threshold=100 — battery is\n"
    "# charged to 100 % every cycle, accelerating wear. Many\n"
    "# laptops support stopping earlier (Lenovo / ASUS /\n"
    "# Framework / HP). Set 75/80 % for daily-driver use :\n"
    "echo 75 | sudo tee /sys/class/power_supply/BAT0/charge_control_start_threshold\n"
    "echo 80 | sudo tee /sys/class/power_supply/BAT0/charge_control_end_threshold\n"
    "# Persist via tlp / battery-charge-limit service."
)


_DEGRADED_RATIO = 0.60
_THRESHOLD_FULL = 100


def _battery_health(d: dict) -> Optional[float]:
    """Returns full / design ratio for a battery, or None."""
    full = d.get("charge_full") or d.get("energy_full")
    design = (d.get("charge_full_design")
                or d.get("energy_full_design"))
    if full and design and design > 0:
        return full / design
    return None


def classify(supplies: list) -> dict:
    if not supplies:
        return {"verdict": "no_power_supply",
                "reason": ("/sys/class/power_supply empty — typical "
                           "for desktops without UPS-HID, servers, "
                           "and VMs."),
                "recommendation": ""}
    batteries = [d for d in supplies
                   if (d.get("type") or "").lower() == "battery"]
    degraded: list = []
    for b in batteries:
        ratio = _battery_health(b)
        if ratio is not None and ratio < _DEGRADED_RATIO:
            degraded.append((b, ratio))
    if degraded:
        names = ", ".join(
            f"{b.get('name')}: {ratio:.0%} health"
            for b, ratio in degraded[:3])
        return {"verdict": "battery_degraded",
                "reason": (f"{len(degraded)} battery/UPS device(s) "
                           f"degraded below 60 % design capacity. "
                           f"{names}"),
                "recommendation": _RECIPE_BATTERY_DEGRADED}
    mains = [d for d in supplies
              if (d.get("type") or "").lower() == "mains"]
    offline_mains = [d for d in mains if d.get("online") == 0]
    if offline_mains and batteries:
        return {"verdict": "no_ac",
                "reason": (f"{len(offline_mains)} mains adapter(s) "
                           f"OFFLINE while battery present — running "
                           f"on battery."),
                "recommendation": _RECIPE_NO_AC}
    unset = [b for b in batteries
              if b.get("charge_control_end_threshold") == _THRESHOLD_FULL]
    if unset:
        names = ", ".join(b.get("name") for b in unset[:3])
        return {"verdict": "charge_threshold_unset",
                "reason": (f"{len(unset)} battery(s) charge to 100 % "
                           f"with no upper-threshold limit set : "
                           f"{names}."),
                "recommendation": _RECIPE_CHARGE_THRESHOLD}
    return {"verdict": "ok",
            "reason": (f"{len(supplies)} power supply device(s) ; "
                       f"{len(batteries)} battery(s), "
                       f"{len(mains)} mains. No degradation, AC "
                       f"present where needed."),
            "recommendation": ""}


def status(cfg=None) -> dict:
    if not os.path.isdir(_SYS_POWER):
        return {
            "ok": False,
            "verdict": {"verdict": "no_power_supply",
                         "reason": ("/sys/class/power_supply absent."),
                         "recommendation": ""},
            "supplies": [],
        }
    supplies = list_supplies(_SYS_POWER)
    verdict = classify(supplies)
    return {
        "ok": True,
        "supply_count": len(supplies),
        "supplies": supplies,
        "verdict": verdict,
    }
