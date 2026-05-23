"""Module regulator_audit — /sys/class/regulator (R&D #61.3).

Reads /sys/class/regulator/regulator.*/{name, type, num_users,
requested_microamps, suspend_disk_state, suspend_mem_state,
suspend_standby_state} + power/runtime_status.

Distinct from R&D #51.1 power_supply_audit (which reads
/sys/class/power_supply — batteries / AC / UPS at the user-visible
end). This module looks at the *kernel regulator framework*
underneath : per-rail consumer counts, suspend-state policy, and
idle-power fingerprint.

Why this matters on a homelab / laptop LLM rig :

* A regulator with `num_users = 0` but still `enabled` (or
  `requested_microamps` non-zero on a rail with no consumers) is
  burning idle watts that no other diagnostic surfaces. On laptop
  hosts this is the difference between a 30 W and a 25 W idle.
* Mismatched suspend-state policy ("on" for suspend-mem when the
  rail should be "disabled") wedges the post-resume path — laptop
  docks misbehave after wake.
* Orphan regulators (no consumers, never bound) are usually a
  driver-load order bug.

Verdicts (priority-ordered) :
  orphan                       ≥1 regulator with num_users=0 AND
                               requested_microamps > 0 — current
                               draw on a rail with no consumer.
  disabled_with_users          ≥1 regulator advertising num_users
                               > 0 but its runtime_status is
                               'suspended'.
  drifted_suspend_state        ≥1 regulator with non-default
                               suspend-state ('on' for both
                               suspend_mem and suspend_disk).
  ok                           regulators consistent.
  unknown                      /sys/class/regulator absent or
                               only contains regulator-dummy.

stdlib only.
"""
from __future__ import annotations

import os
import re
from typing import List, Optional


NAME = "regulator_audit"


_SYS_REGULATOR = "/sys/class/regulator"

_REGULATOR_DIR_RE = re.compile(r"^regulator\.\d+$")


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


def list_regulators(sys_reg: str = _SYS_REGULATOR) -> List[dict]:
    if not os.path.isdir(sys_reg):
        return []
    out: List[dict] = []
    for name in sorted(os.listdir(sys_reg)):
        if not _REGULATOR_DIR_RE.match(name):
            continue
        d = os.path.join(sys_reg, name)
        out.append({
            "id": name,
            "name": _read(os.path.join(d, "name")),
            "type": _read(os.path.join(d, "type")),
            "num_users": _read_int(
                os.path.join(d, "num_users")),
            "requested_microamps": _read_int(
                os.path.join(d, "requested_microamps")),
            "suspend_mem_state": _read(
                os.path.join(d, "suspend_mem_state")),
            "suspend_disk_state": _read(
                os.path.join(d, "suspend_disk_state")),
            "suspend_standby_state": _read(
                os.path.join(d, "suspend_standby_state")),
            "runtime_status": _read(
                os.path.join(d, "power", "runtime_status")),
        })
    return out


def _is_real_regulator(r: dict) -> bool:
    name = (r.get("name") or "").lower()
    return name not in ("regulator-dummy", "")


def classify(regulators: List[dict]) -> dict:
    real = [r for r in regulators if _is_real_regulator(r)]
    if not real:
        return {"verdict": "unknown",
                "reason": ("/sys/class/regulator absent or only "
                          "contains regulator-dummy."),
                "recommendation": ""}

    # 1) orphan — num_users=0 but requested_microamps > 0
    orphans = [r for r in real
                  if (r.get("num_users") == 0 and
                      (r.get("requested_microamps") or 0) > 0)]
    if orphans:
        sample = ", ".join(r["name"] for r in orphans[:3])
        return {"verdict": "orphan",
                "reason": (f"{len(orphans)} regulator(s) with "
                          f"requested_microamps > 0 but no users : "
                          f"{sample}. Idle watts wasted."),
                "recommendation": _recipe_orphan()}

    # 2) disabled_with_users — runtime_status 'suspended' yet
    #    num_users > 0
    disabled = [r for r in real
                   if (r.get("runtime_status") == "suspended" and
                       (r.get("num_users") or 0) > 0)]
    if disabled:
        sample = ", ".join(r["name"] for r in disabled[:3])
        return {"verdict": "disabled_with_users",
                "reason": (f"{len(disabled)} regulator(s) suspended "
                          f"with consumers : {sample}. Hardware "
                          f"trying to use a rail the PM core gave "
                          f"up on."),
                "recommendation": _recipe_disabled_with_users()}

    # 3) drifted_suspend_state — both suspend_mem and suspend_disk
    #    explicitly 'on' (instead of unset / disabled)
    drifted = [r for r in real
                  if (r.get("suspend_mem_state") == "on" and
                      r.get("suspend_disk_state") == "on")]
    if drifted:
        sample = ", ".join(r["name"] for r in drifted[:3])
        return {"verdict": "drifted_suspend_state",
                "reason": (f"{len(drifted)} regulator(s) with "
                          f"suspend_mem AND suspend_disk both = "
                          f"'on' : {sample}. Resume path may "
                          f"wedge."),
                "recommendation": _recipe_drifted()}

    return {"verdict": "ok",
            "reason": (f"{len(real)} regulator(s) — consumers / "
                      f"suspend states consistent."),
            "recommendation": ""}


def status(config=None, sys_reg: str = _SYS_REGULATOR) -> dict:
    regulators = list_regulators(sys_reg)
    ok = bool(regulators)
    verdict = classify(regulators)
    return {"ok": ok,
              "regulator_count": len(regulators),
              "regulators": regulators,
              "verdict": verdict}


# ── recovery recipes ────────────────────────────────────────────

def _recipe_orphan() -> str:
    return ("# Identify which driver claims the orphan rail :\n"
            "for r in /sys/class/regulator/regulator.*; do\n"
            "  u=$(cat $r/num_users) ua=$(cat $r/requested_microamps)\n"
            "  if [ \"$u\" = 0 ] && [ \"$ua\" != 0 ]; then\n"
            "    echo \"$(cat $r/name) : $ua µA orphaned\"\n"
            "  fi\n"
            "done\n"
            "# Usually means a driver registered the rail then\n"
            "# failed to bind. dmesg | grep regulator should tell.\n")


def _recipe_disabled_with_users() -> str:
    return ("# Find the bad consumer :\n"
            "for r in /sys/class/regulator/regulator.*; do\n"
            "  if [ \"$(cat $r/power/runtime_status 2>/dev/null)\" = suspended ]; then\n"
            "    echo \"$(cat $r/name) suspended, users=$(cat $r/num_users)\"\n"
            "  fi\n"
            "done\n"
            "# Force-resume the rail :\n"
            "echo on | sudo tee /sys/class/regulator/regulator.<id>/power/control\n")


def _recipe_drifted() -> str:
    return ("# Inspect the suspend-state matrix :\n"
            "for r in /sys/class/regulator/regulator.*; do\n"
            "  echo \"$(cat $r/name) : mem=$(cat $r/suspend_mem_state) \\\n"
            "    disk=$(cat $r/suspend_disk_state)\"\n"
            "done | head -20\n"
            "# Vendor-provided sysfs override or kernel cmdline\n"
            "# regulator.suspend_state= can fix this.\n")
