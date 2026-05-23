"""Module cooling_devices — thermal cooling-device inventory (R&D #42.3).

Shipped #21 thermal_zones covers the *sensor* side of the thermal
subsystem — every /sys/class/thermal/thermal_zone*/temp + trip points.
This module covers the *actuator* side : /sys/class/thermal/
cooling_device*/* — Processor (per-CPU throttling), Fan (when ACPI
fan tables are present), intel_powerclamp (idle-injection), nvidia
cdevs (when nvidia.ko registers thermal cooling), PCIe_Port_Link_
Speed_<BDF> (link-width fallback), TPI (Thermal Pressure Index).

Each cooling device exposes :
  type        the driver registering the actuator
  cur_state   current throttle level (0 = off, max_state = full)
  max_state   maximum throttle level

And each thermal_zone exposes its bindings via :
  thermal_zone<X>/cdev<Y>             symlink → cooling_device<N>
  thermal_zone<X>/cdev<Y>_trip_point  trip index this cdev responds to
  thermal_zone<X>/cdev<Y>_weight      relative priority across cdevs

Verdicts (priority-ordered) :
  saturated_cdev          ≥1 cdev has cur_state == max_state → the
                          actuator is *pegged*, the box is being held
                          back by this throttle. Surface "which cdev,
                          tied to which thermal_zone, at which trip".
  unbound_zone            thermal_zone exists with > 0 trip points
                          but no cdev binding → trip will fire but
                          nothing will respond. ACPI table flaw or
                          missing kernel module.
  no_cooling              cooling_device count = 0 → kernel thermal
                          framework isn't registering any actuator
                          (no Processor cdev, no Fan, no PCIe-link
                          cdev). Hypervisor / minimal kernel.
  ok                      cdevs present, none saturated, each non-
                          trivial zone has at least one binding.
  unknown                 /sys/class/thermal unreadable.

stdlib only.
"""
from __future__ import annotations

import os
import re
from typing import Optional


NAME = "cooling_devices"


_SYS_THERMAL = "/sys/class/thermal"


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


def _cdev_number(name: str) -> Optional[int]:
    m = re.match(r"^cooling_device(\d+)$", name)
    return int(m.group(1)) if m else None


def list_cooling_devices(sys_thermal: str = _SYS_THERMAL) -> list:
    if not os.path.isdir(sys_thermal):
        return []
    out: list = []
    for name in sorted(os.listdir(sys_thermal)):
        n = _cdev_number(name)
        if n is None:
            continue
        ddir = os.path.join(sys_thermal, name)
        out.append({
            "name": name,
            "index": n,
            "type": (_read(os.path.join(ddir, "type"))
                       or "").strip() or None,
            "cur_state": _read_int(os.path.join(ddir, "cur_state")),
            "max_state": _read_int(os.path.join(ddir, "max_state")),
        })
    out.sort(key=lambda d: d["index"])
    return out


def list_thermal_zones(sys_thermal: str = _SYS_THERMAL) -> list:
    if not os.path.isdir(sys_thermal):
        return []
    return sorted(n for n in os.listdir(sys_thermal)
                    if n.startswith("thermal_zone")
                    and n[len("thermal_zone"):].isdigit())


def read_zone_bindings(sys_thermal: str, zone: str) -> dict:
    """Return {"type": <zone type>, "trips": <count of trip points
    declared>, "bindings": [{"cdev_index": N, "trip_point": X,
    "weight": W}], "cdevs_present": [<cdev<Y>>...]}"""
    zdir = os.path.join(sys_thermal, zone)
    ztype = (_read(os.path.join(zdir, "type")) or "").strip() or None
    trip_count = 0
    bindings: list = []
    cdevs_present: list = []
    try:
        names = os.listdir(zdir)
    except OSError:
        names = []
    for n in names:
        if n.startswith("trip_point_") and n.endswith("_type"):
            trip_count += 1
            continue
        m = re.match(r"^cdev(\d+)$", n)
        if not m:
            continue
        cdevs_present.append(n)
        cdev_idx_str = m.group(1)
        # Resolve symlink → cooling_device<N>
        target = None
        try:
            link = os.readlink(os.path.join(zdir, n))
            target = os.path.basename(link)
        except OSError:
            pass
        cdev_index: Optional[int] = None
        if target:
            cdev_index = _cdev_number(target)
        trip_point = _read_int(os.path.join(
            zdir, f"cdev{cdev_idx_str}_trip_point"))
        weight = _read_int(os.path.join(
            zdir, f"cdev{cdev_idx_str}_weight"))
        bindings.append({
            "cdev_slot": int(cdev_idx_str),
            "cdev_target": target,
            "cdev_index": cdev_index,
            "trip_point": trip_point,
            "weight": weight,
        })
    bindings.sort(key=lambda b: b["cdev_slot"])
    return {"zone": zone, "type": ztype,
              "trip_count": trip_count,
              "bindings": bindings,
              "cdevs_present_count": len(cdevs_present)}


_RECIPE_SATURATED = (
    "# Cooling device(s) are pegged at max — the box is being held\n"
    "# back by this actuator. Investigate the trip-temperature in\n"
    "# /sys/class/thermal/thermal_zone*/trip_point_*_temp and either\n"
    "# raise the trip (if conservative for your chassis) or improve\n"
    "# cooling. Quick visibility :\n"
    "for cd in /sys/class/thermal/cooling_device*; do\n"
    "  type=$(cat $cd/type) cur=$(cat $cd/cur_state) max=$(cat $cd/max_state)\n"
    "  [ \"${cur:-0}\" -ge \"${max:-1}\" ] && [ \"${max:-0}\" -gt 0 ] && \\\n"
    "    echo \"$cd ($type) cur=$cur max=$max\"\n"
    "done"
)

_RECIPE_UNBOUND = (
    "# thermal_zone declares trip points but no cdev is bound — when\n"
    "# the trip fires, nothing will throttle in response. Likely a\n"
    "# missing platform driver. Investigate :\n"
    "ls /sys/class/thermal/thermal_zone*/\n"
    "# And the ACPI thermal tables :\n"
    "ls /sys/firmware/acpi/tables/ | grep -E '^DSDT|^SSDT'\n"
    "# Modules to consider loading : processor_thermal_device,\n"
    "# int340x_thermal, x86_pkg_temp_thermal, acpi_thermal_rel."
)

_RECIPE_NO_COOLING = (
    "# No cooling actuators registered with the kernel thermal\n"
    "# framework — likely a hypervisor guest, a minimal kernel, or\n"
    "# CONFIG_THERMAL=n. If on bare-metal, ensure modules like\n"
    "# processor_thermal_device + intel_powerclamp are loaded :\n"
    "sudo modprobe processor_thermal_device\n"
    "sudo modprobe intel_powerclamp"
)


def classify(cdevs: list, zones_data: list) -> dict:
    if not cdevs and not zones_data:
        return {"verdict": "unknown",
                "reason": "/sys/class/thermal unreadable.",
                "recommendation": ""}
    if not cdevs:
        return {"verdict": "no_cooling",
                "reason": ("No cooling_device* entries — kernel "
                           "thermal framework is not registering "
                           "any actuator (Processor / Fan / "
                           "PCIe-link / intel_powerclamp). Likely "
                           "a hypervisor guest or minimal kernel."),
                "recommendation": _RECIPE_NO_COOLING}
    saturated = [
        c for c in cdevs
        if isinstance(c.get("max_state"), int) and c["max_state"] > 0
        and isinstance(c.get("cur_state"), int)
        and c["cur_state"] >= c["max_state"]
    ]
    if saturated:
        names = ", ".join(
            f"{c['name']} ({c['type'] or '?'}) "
            f"{c['cur_state']}/{c['max_state']}"
            for c in saturated)
        return {"verdict": "saturated_cdev",
                "reason": (f"{len(saturated)} cooling device(s) "
                           f"pegged at max throttle — actuator "
                           f"is the bottleneck. {names}"),
                "recommendation": _RECIPE_SATURATED}
    # Unbound: zone has trip points but no cdev binding.
    unbound = [
        z for z in zones_data
        if z["trip_count"] > 0 and len(z["bindings"]) == 0
    ]
    if unbound:
        names = ", ".join(
            f"{z['zone']} ({z['type'] or '?'}) "
            f"trips={z['trip_count']}" for z in unbound)
        return {"verdict": "unbound_zone",
                "reason": (f"{len(unbound)} thermal_zone(s) declare "
                           f"trip points with no cooling-device "
                           f"binding — trips will fire with no "
                           f"actuator to throttle. {names}"),
                "recommendation": _RECIPE_UNBOUND}
    return {"verdict": "ok",
            "reason": (f"{len(cdevs)} cooling device(s) "
                       f"registered ; none saturated. "
                       f"{len(zones_data)} thermal zone(s)."),
            "recommendation": ""}


def status(cfg=None) -> dict:
    if not os.path.isdir(_SYS_THERMAL):
        return {
            "ok": False,
            "verdict": {"verdict": "unknown",
                         "reason": ("/sys/class/thermal "
                                    "unreadable."),
                         "recommendation": ""},
            "cooling_devices": [],
            "thermal_zones": [],
        }
    cdevs = list_cooling_devices(_SYS_THERMAL)
    zones = list_thermal_zones(_SYS_THERMAL)
    zones_data = [read_zone_bindings(_SYS_THERMAL, z) for z in zones]
    verdict = classify(cdevs, zones_data)
    return {
        "ok": True,
        "cooling_device_count": len(cdevs),
        "thermal_zone_count": len(zones),
        "cooling_devices": cdevs,
        "thermal_zones": zones_data,
        "verdict": verdict,
    }
