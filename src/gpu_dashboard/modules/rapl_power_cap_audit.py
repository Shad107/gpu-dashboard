"""Module rapl_power_cap_audit — RAPL + cpufreq throttling (R&D #53.4).

Reads :
  /sys/class/powercap/intel-rapl*/intel-rapl:*/{name,enabled,
    constraint_0_power_limit_uw,constraint_0_time_window_us,
    constraint_1_power_limit_uw,max_power_range_uw}
  /sys/devices/system/cpu/cpufreq/boost
  /sys/devices/system/cpu/intel_pstate/{no_turbo,max_perf_pct,
    energy_efficiency}
  /sys/devices/system/cpu/cpu*/cpufreq/scaling_governor

Catches :

* Vendor BIOS or `power-profiles-daemon` clamps RAPL PL1 below the
  CPU's nameplate TDP on a laptop / SFF rig — LLM prefill becomes
  CPU-bound at 60-70 % of expected tokens/sec, with NO visible
  thermal trip (RAPL is hard-cap, not thermal).
* CPU turbo silently disabled via /sys/.../intel_pstate/no_turbo=1
  or cpufreq/boost=0 — single-thread latency collapses by 30 %.
* Mixed governors across CPUs (some 'powersave', others
  'performance') — usually a half-applied tuning attempt.

Verdicts (priority-ordered) :
  pl1_below_tdp_throttling     PL1 (long-term cap) < max_power_range
                               for the 'package-0' RAPL zone.
  governor_powersave_mixed     ≥2 distinct governors across CPUs,
                               OR any CPU on 'powersave' with
                               others on 'performance' / 'schedutil'.
  turbo_disabled_silently      intel_pstate/no_turbo = 1 OR
                               cpufreq/boost = 0.
  psys_cap_active              a 'psys' (platform-wide) RAPL zone
                               exists AND its PL1 is below the
                               package PL1 → platform cap clamps
                               the CPU silently.
  ok                           PL1 ≈ nameplate, governors uniform.
  unknown                      no RAPL + no cpufreq sysfs (VM /
                               kernel built without these).

stdlib only.
"""
from __future__ import annotations

import os
import re
from typing import Dict, List, Optional


NAME = "rapl_power_cap_audit"


_SYS_POWERCAP = "/sys/class/powercap"
_SYS_CPU = "/sys/devices/system/cpu"

_CPUFREQ_BOOST = "/sys/devices/system/cpu/cpufreq/boost"
_INTEL_PSTATE = "/sys/devices/system/cpu/intel_pstate"

_CPU_DIR_RE = re.compile(r"^cpu(\d+)$")
_RAPL_ZONE_RE = re.compile(r"^intel-rapl:\d+(?::\d+)*$")


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


def list_rapl_zones(sys_powercap: str = _SYS_POWERCAP) -> List[dict]:
    if not os.path.isdir(sys_powercap):
        return []
    out: List[dict] = []
    for name in sorted(os.listdir(sys_powercap)):
        if not _RAPL_ZONE_RE.match(name):
            continue
        zd = os.path.join(sys_powercap, name)
        zone = {
            "id": name,
            "name": _read(os.path.join(zd, "name")),
            "enabled": _read_int(os.path.join(zd, "enabled")),
            "constraint_0_power_limit_uw":
                _read_int(os.path.join(
                    zd, "constraint_0_power_limit_uw")),
            "constraint_0_time_window_us":
                _read_int(os.path.join(
                    zd, "constraint_0_time_window_us")),
            "constraint_1_power_limit_uw":
                _read_int(os.path.join(
                    zd, "constraint_1_power_limit_uw")),
            "max_power_range_uw":
                _read_int(os.path.join(zd, "max_power_range_uw")),
        }
        out.append(zone)
    return out


def list_governors(sys_cpu: str = _SYS_CPU) -> Dict[int, Optional[str]]:
    out: Dict[int, Optional[str]] = {}
    if not os.path.isdir(sys_cpu):
        return out
    for name in sorted(os.listdir(sys_cpu)):
        m = _CPU_DIR_RE.match(name)
        if not m:
            continue
        idx = int(m.group(1))
        gov = _read(os.path.join(sys_cpu, name, "cpufreq",
                                      "scaling_governor"))
        out[idx] = gov
    return out


def read_turbo_state(boost_path: str = _CPUFREQ_BOOST,
                       intel_pstate: str = _INTEL_PSTATE) -> dict:
    out: dict = {"cpufreq_boost": _read_int(boost_path)}
    if os.path.isdir(intel_pstate):
        out["intel_pstate_no_turbo"] = _read_int(
            os.path.join(intel_pstate, "no_turbo"))
        out["intel_pstate_max_perf_pct"] = _read_int(
            os.path.join(intel_pstate, "max_perf_pct"))
        out["intel_pstate_energy_efficiency"] = _read_int(
            os.path.join(intel_pstate, "energy_efficiency"))
    return out


def classify(zones: List[dict], governors: Dict[int, Optional[str]],
              turbo: dict) -> dict:
    has_governor_data = any(g for g in governors.values())
    if not zones and not has_governor_data:
        return {"verdict": "unknown",
                "reason": ("No RAPL zones and no cpufreq sysfs — "
                          "VM / minimal kernel ?"),
                "recommendation": ""}

    # 1) pl1_below_tdp_throttling on the package-0 zone (depth 0)
    pkg = next((z for z in zones
                  if z["id"].count(":") == 1 and
                     (z.get("name") or "").startswith("package")),
                 None)
    if pkg and pkg.get("constraint_0_power_limit_uw") and \
            pkg.get("max_power_range_uw"):
        pl1 = pkg["constraint_0_power_limit_uw"]
        cap = pkg["max_power_range_uw"]
        if cap > 0 and pl1 < cap * 0.80:
            pl1_w = pl1 / 1_000_000
            cap_w = cap / 1_000_000
            return {"verdict": "pl1_below_tdp_throttling",
                    "reason": (f"RAPL package PL1 = {pl1_w:.1f} W "
                              f"< 80 % of max_power_range "
                              f"{cap_w:.1f} W → CPU caps itself "
                              f"under sustained load."),
                    "recommendation": _recipe_raise_pl1(pkg["id"])}

    # 2) governor_powersave_mixed
    gov_values = [g for g in governors.values() if g]
    if gov_values:
        uniq = set(gov_values)
        if len(uniq) > 1:
            return {"verdict": "governor_powersave_mixed",
                    "reason": (f"CPUs run distinct cpufreq "
                              f"governors : {sorted(uniq)}. Half-"
                              f"applied tuning — outcome depends on "
                              f"which CPU schedules your inference."),
                    "recommendation": _recipe_unify_governor()}

    # 3) turbo_disabled_silently
    if turbo.get("intel_pstate_no_turbo") == 1 or \
            turbo.get("cpufreq_boost") == 0:
        return {"verdict": "turbo_disabled_silently",
                "reason": ("CPU turbo / boost is disabled — single-"
                          "thread latency / prefill suffers."),
                "recommendation": _recipe_enable_turbo()}

    # 4) psys_cap_active — platform-wide RAPL clamps below package
    psys = next((z for z in zones
                   if (z.get("name") or "").startswith("psys")),
                  None)
    if psys and pkg and \
            psys.get("constraint_0_power_limit_uw") and \
            pkg.get("constraint_0_power_limit_uw") and \
            psys["constraint_0_power_limit_uw"] < \
            pkg["constraint_0_power_limit_uw"]:
        return {"verdict": "psys_cap_active",
                "reason": (f"'psys' RAPL PL1 "
                          f"{psys['constraint_0_power_limit_uw']/1e6:.1f} W "
                          f"is below package PL1 "
                          f"{pkg['constraint_0_power_limit_uw']/1e6:.1f} W "
                          f"— platform-wide clamp."),
                "recommendation": _recipe_psys_clamp()}

    return {"verdict": "ok",
            "reason": (f"{len(zones)} RAPL zone(s), "
                      f"{len(governors)} CPU(s) ; caps and "
                      f"governor look uniform."),
            "recommendation": ""}


def status(config=None,
            sys_powercap: str = _SYS_POWERCAP,
            sys_cpu: str = _SYS_CPU,
            boost_path: str = _CPUFREQ_BOOST,
            intel_pstate: str = _INTEL_PSTATE) -> dict:
    zones = list_rapl_zones(sys_powercap)
    governors = list_governors(sys_cpu)
    turbo = read_turbo_state(boost_path, intel_pstate)
    ok = bool(zones or governors)
    verdict = classify(zones, governors, turbo)
    # Compact governor histogram for the UI :
    gov_hist: Dict[str, int] = {}
    for g in governors.values():
        if g is None:
            continue
        gov_hist[g] = gov_hist.get(g, 0) + 1
    return {"ok": ok,
              "zone_count": len(zones),
              "zones": zones,
              "cpu_count": len(governors),
              "governor_histogram": gov_hist,
              "turbo": turbo,
              "verdict": verdict}


# ── recovery recipes ────────────────────────────────────────────

def _recipe_raise_pl1(zone_id: str) -> str:
    return ("# Raise RAPL package PL1 to the CPU nameplate TDP :\n"
            f"echo $((125 * 1000000)) | sudo tee \\\n"
            f"  /sys/class/powercap/{zone_id}/constraint_0_power_limit_uw\n"
            "# Replace 125 with your actual nameplate W.\n"
            "# If a daemon ( power-profiles-daemon, tuned, tlp )\n"
            "# rewrites this, disable or reconfigure it :\n"
            "systemctl status power-profiles-daemon tuned tlp 2>/dev/null\n")


def _recipe_unify_governor() -> str:
    return ("# Force a uniform cpufreq governor across all CPUs :\n"
            "echo performance | sudo tee /sys/devices/system/cpu/cpu*/cpufreq/scaling_governor\n"
            "# Persist via cpupower / tuned / systemd unit. Common\n"
            "# culprit : a tuned profile only set some CPUs.\n")


def _recipe_enable_turbo() -> str:
    return ("# Re-enable boost / turbo :\n"
            "echo 0 | sudo tee /sys/devices/system/cpu/intel_pstate/no_turbo  2>/dev/null || true\n"
            "echo 1 | sudo tee /sys/devices/system/cpu/cpufreq/boost          2>/dev/null || true\n"
            "# Then check : cat /sys/devices/system/cpu/cpu0/cpufreq/cpuinfo_max_freq\n")


def _recipe_psys_clamp() -> str:
    return ("# A platform-wide RAPL ('psys') zone clamps the CPU.\n"
            "# This is usually set by laptop EC firmware. Check your\n"
            "# BIOS for 'Power Limit 4' / 'platform power' settings\n"
            "# and raise / disable as appropriate.\n")
