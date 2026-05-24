"""Module thermal_trip_drift_audit — thermal-zone trip-
point configuration drift (R&D #81.4).

Existing thermal_zones reads current temperatures and
classifies them cool/warm/hot/critical. cooling_devices
maps cdev→trip bindings. Neither checks whether trip
*thresholds* have drifted (firmware reset, EC quirk) or
whether hysteresis = 0 is causing fan oscillation.

Critical for RTX 3090 + AIO desktops where one wrong
trip wastes thermal headroom — or worse, melts a VRM
because the passive trip got disabled.

Reads, per /sys/class/thermal/thermal_zone<N>/ :

  type                  zone label (x86_pkg_temp, acpitz, …)
  temp                  current temperature (millidegrees C)
  policy                current cooling governor
  available_policies    governors the kernel can switch to
  trip_point_<i>_temp   threshold in millidegrees C
  trip_point_<i>_type   "critical", "hot", "passive",
                        "active", "engaged"
  trip_point_<i>_hyst   hysteresis (millidegrees) — must
                        be > 0 to avoid on/off oscillation

Verdicts (worst first) :

  trip_below_current_temp     a non-critical trip is set
                              below current temp — the
                              kernel should already have
                              fired throttling, but the
                              trip never armed.
  hyst_zero_oscillation_risk  any trip has hyst = 0 — fans /
                              passive cooling will flap.
  passive_disabled_on_cpu_zone  CPU zone (x86_pkg_temp or
                                similar) has zero passive
                                trips configured —
                                cpufreq throttling won't
                                kick in before hot/critical.
  policy_user_space_idle      policy = "user_space" with
                              no userland daemon driving —
                              cooling is effectively off.
  ok                          all zones healthy.
  unknown                     /sys/class/thermal has no
                              thermal_zone* dirs.
"""
from __future__ import annotations

import os
import re
from typing import Optional

DEFAULT_THERMAL_ROOT = "/sys/class/thermal"

# Patterns
_TRIP_RE = re.compile(r"^trip_point_(\d+)_(temp|type|hyst)$")
_CPU_ZONE_RE = re.compile(
    r"^(x86_pkg_temp|.*[_-]thermal|coretemp|cpu_thermal)$",
    re.IGNORECASE)


def _read_text(path: str) -> Optional[str]:
    try:
        with open(path, "r", encoding="utf-8") as fh:
            return fh.read().strip()
    except (OSError, PermissionError):
        return None


def _read_int(path: str) -> Optional[int]:
    s = _read_text(path)
    if s is None:
        return None
    try:
        return int(s)
    except ValueError:
        return None


def list_zones(root: str = DEFAULT_THERMAL_ROOT) -> list[str]:
    try:
        return sorted(
            n for n in os.listdir(root)
            if re.match(r"^thermal_zone\d+$", n))
    except OSError:
        return []


def read_zone(root: str, zone: str) -> dict:
    """Returns parsed zone info incl. trip points list."""
    d = os.path.join(root, zone)
    out: dict = {
        "zone": zone,
        "type": _read_text(os.path.join(d, "type")),
        "temp": _read_int(os.path.join(d, "temp")),
        "policy": _read_text(os.path.join(d, "policy")),
        "available_policies": (
            _read_text(os.path.join(d, "available_policies"))
            or "").split(),
        "trips": [],
    }
    # Discover trip-point indices by globbing the dir
    trip_idx: dict[int, dict] = {}
    try:
        entries = os.listdir(d)
    except OSError:
        entries = []
    for entry in entries:
        m = _TRIP_RE.match(entry)
        if m is None:
            continue
        idx = int(m.group(1))
        kind = m.group(2)
        trip_idx.setdefault(idx, {"index": idx})
        if kind == "type":
            trip_idx[idx]["type"] = _read_text(
                os.path.join(d, entry))
        else:
            trip_idx[idx][kind] = _read_int(
                os.path.join(d, entry))
    out["trips"] = [trip_idx[i] for i in sorted(trip_idx)]
    return out


def _is_cpu_zone(zone_type: Optional[str]) -> bool:
    if not zone_type:
        return False
    return bool(_CPU_ZONE_RE.match(zone_type))


def classify(zones: list[dict]) -> dict:
    if not zones:
        return {"verdict": "unknown",
                "reason": "/sys/class/thermal has no "
                          "thermal_zone* dirs."}

    # 1. err — current temp already past a non-critical trip
    for z in zones:
        cur = z.get("temp")
        if cur is None:
            continue
        for t in z.get("trips", []):
            t_temp = t.get("temp")
            t_type = t.get("type")
            if t_temp is None or t_type is None:
                continue
            if t_type == "critical":
                continue  # critical past is a separate audit
            if t_temp <= 0:
                continue  # disabled trips report 0 or negative
            if cur >= t_temp:
                return {
                    "verdict": "trip_below_current_temp",
                    "reason": (
                        f"{z['zone']} ({z.get('type')}) "
                        f"temp {cur/1000:.1f}°C ≥ "
                        f"{t_type} trip "
                        f"{t_temp/1000:.1f}°C — kernel "
                        "should have fired throttling."),
                    "zone": z["zone"],
                    "current_c": cur / 1000,
                    "trip_c": t_temp / 1000,
                    "trip_type": t_type}

    # 2. warn — hysteresis = 0 (oscillation risk)
    for z in zones:
        for t in z.get("trips", []):
            t_temp = t.get("temp")
            t_hyst = t.get("hyst")
            t_type = t.get("type")
            if (t_temp is not None and t_temp > 0
                    and t_hyst == 0
                    and t_type in ("passive", "active")):
                return {
                    "verdict": "hyst_zero_oscillation_risk",
                    "reason": (
                        f"{z['zone']} ({z.get('type')}) "
                        f"{t_type} trip at "
                        f"{t_temp/1000:.1f}°C has "
                        "hysteresis = 0 — fan / cooling "
                        "will flap on / off."),
                    "zone": z["zone"],
                    "trip_index": t["index"]}

    # 3. accent — CPU zone with no passive trips
    for z in zones:
        if not _is_cpu_zone(z.get("type")):
            continue
        has_passive = any(
            t.get("type") == "passive"
            and (t.get("temp") or 0) > 0
            for t in z.get("trips", []))
        if not has_passive:
            return {
                "verdict": "passive_disabled_on_cpu_zone",
                "reason": (
                    f"CPU zone {z['zone']} "
                    f"({z.get('type')}) has no enabled "
                    "passive trip — cpufreq throttling "
                    "won't kick in before hot/critical."),
                "zone": z["zone"]}

    # 4. accent — user_space policy idle
    for z in zones:
        policy = z.get("policy")
        avail = z.get("available_policies", [])
        if (policy == "user_space"
                and "step_wise" in avail):
            return {
                "verdict": "policy_user_space_idle",
                "reason": (
                    f"{z['zone']} ({z.get('type')}) "
                    "policy = user_space and kernel "
                    "policies are available — userland "
                    "may not be driving cooling."),
                "zone": z["zone"]}

    return {"verdict": "ok",
            "reason": (
                f"{len(zones)} thermal zone(s) audited ; "
                "trips configured, hysteresis non-zero.")}


def status(config: Optional[dict] = None,
           root: str = DEFAULT_THERMAL_ROOT) -> dict:
    zones = [read_zone(root, z) for z in list_zones(root)]
    verdict = classify(zones)
    return {
        "ok": verdict["verdict"] not in (
            "unknown", "trip_below_current_temp"),
        "zone_count": len(zones),
        "zones": [
            {"zone": z["zone"], "type": z["type"],
             "temp_c": (z["temp"] / 1000
                          if z["temp"] is not None else None),
             "policy": z["policy"],
             "trip_count": len(z["trips"])}
            for z in zones],
        "verdict": verdict,
    }
