"""Module remoteproc_coprocessor_audit — Linux remoteproc
framework audit (R&D #70.1).

The `remoteproc` subsystem manages on-die coprocessors that
run their own firmware separately from the main CPU :

  * ARM Cortex-M / Cortex-R cores on SoCs (Qualcomm DSP,
    TI C66x, NXP M4 in i.MX).
  * Intel ME / CSME stubs that expose a remoteproc view.
  * NVIDIA Falcon engines (PSP, Tegra BPMP) — rare but real.

Each registered coprocessor surfaces a small sysfs interface
under /sys/class/remoteproc/remoteproc<N>/ :

  state         "offline" | "suspended" | "running" |
                "crashed" | "deleted"
  name          driver-level name
  firmware      requested firmware filename (in /lib/firmware/)
  recovery      "enabled" | "disabled"
  crash_count   monotonic crash counter (kernel ≥ 5.18 ; on
                older kernels this file may be absent)

For a homelab/desktop, the audit catches two real classes of
trouble :

* A crashed coprocessor with recovery=disabled — the firmware
  is dead and nothing will resurrect it. Often blocks suspend
  / resume on laptops with Intel ME.
* A `state=offline` coprocessor when its `recovery` knob is
  enabled — typically means the firmware blob is missing.

Verdicts (priority order) :
  remoteproc_crashed       ≥1 remoteproc state == "crashed" OR
                             crash_count > 0.
  recovery_disabled        ≥1 remoteproc has recovery
                             "disabled".
  firmware_missing         remoteproc state == "offline" AND
                             firmware filename present (the
                             driver is waiting on a blob).
  state_offline            ≥1 remoteproc has state "offline"
                             without firmware metadata.
  ok                       all running / suspended cleanly.
  unknown                  /sys/class/remoteproc absent (no
                             coprocessors registered).

stdlib only.
"""
from __future__ import annotations

import os
from typing import List, Optional


NAME = "remoteproc_coprocessor_audit"


_SYS_REMOTEPROC = "/sys/class/remoteproc"


def _read(path: str) -> Optional[str]:
    try:
        with open(path) as f:
            return f.read().strip()
    except OSError:
        return None


def _read_int(path: str) -> Optional[int]:
    t = _read(path)
    if t is None:
        return None
    try:
        return int(t)
    except ValueError:
        return None


def list_remoteprocs(sys_path: str = _SYS_REMOTEPROC
                          ) -> List[dict]:
    if not os.path.isdir(sys_path):
        return []
    try:
        names = sorted(os.listdir(sys_path))
    except OSError:
        return []
    out: List[dict] = []
    for n in names:
        d = os.path.join(sys_path, n)
        if not os.path.isdir(d):
            continue
        out.append({
            "id": n,
            "state": _read(os.path.join(d, "state")),
            "name": _read(os.path.join(d, "name")),
            "firmware": _read(os.path.join(d, "firmware")),
            "recovery": _read(os.path.join(d, "recovery")),
            "crash_count": _read_int(os.path.join(
                d, "crash_count")),
        })
    return out


def classify(remoteprocs: List[dict],
              path_present: bool) -> dict:
    if not path_present:
        return {"verdict": "unknown",
                "reason": ("/sys/class/remoteproc absent — no "
                          "on-die coprocessors are exposed "
                          "(typical on x86 desktop / KVM guest)."),
                "recommendation": ""}

    if not remoteprocs:
        return {"verdict": "unknown",
                "reason": ("/sys/class/remoteproc exists but is "
                          "empty — driver framework loaded with "
                          "no devices."),
                "recommendation": ""}

    # 1) remoteproc_crashed
    crashed = [r for r in remoteprocs
                  if r.get("state") == "crashed"
                  or (r.get("crash_count") or 0) > 0]
    if crashed:
        sample = ", ".join(
            f"{r['id']} state={r.get('state')} "
            f"crashes={r.get('crash_count')}"
                for r in crashed[:3])
        return {"verdict": "remoteproc_crashed",
                "reason": (f"{len(crashed)} remoteproc(s) crashed "
                          f"or have non-zero crash_count : "
                          f"{sample}."),
                "recommendation": _recipe_crashed()}

    # 2) recovery_disabled
    recov_off = [r for r in remoteprocs
                    if (r.get("recovery") or "").lower()
                          == "disabled"]
    if recov_off:
        sample = ", ".join(r["id"] for r in recov_off[:3])
        return {"verdict": "recovery_disabled",
                "reason": (f"{len(recov_off)} remoteproc(s) have "
                          f"recovery disabled : {sample}. "
                          f"A future crash will be permanent."),
                "recommendation": _recipe_recovery_off()}

    # 3) firmware_missing — offline + firmware filename set
    fw_waiting = [r for r in remoteprocs
                      if r.get("state") == "offline"
                      and r.get("firmware")]
    if fw_waiting:
        sample = ", ".join(
            f"{r['id']} fw={r.get('firmware')}"
                for r in fw_waiting[:3])
        return {"verdict": "firmware_missing",
                "reason": (f"{len(fw_waiting)} remoteproc(s) are "
                          f"offline awaiting firmware : "
                          f"{sample}."),
                "recommendation": _recipe_fw_missing()}

    # 4) state_offline
    offline = [r for r in remoteprocs
                  if r.get("state") == "offline"]
    if offline:
        sample = ", ".join(r["id"] for r in offline[:3])
        return {"verdict": "state_offline",
                "reason": (f"{len(offline)} remoteproc(s) offline "
                          f": {sample}."),
                "recommendation": _recipe_offline()}

    return {"verdict": "ok",
            "reason": (f"{len(remoteprocs)} remoteproc(s), all "
                      f"in a healthy state."),
            "recommendation": ""}


def status(config=None,
            sys_path: str = _SYS_REMOTEPROC) -> dict:
    path_present = os.path.isdir(sys_path)
    remoteprocs = list_remoteprocs(sys_path)
    verdict = classify(remoteprocs, path_present)
    return {"ok": path_present,
              "path_present": path_present,
              "remoteproc_count": len(remoteprocs),
              "remoteprocs": remoteprocs,
              "verdict": verdict}


# ── recovery recipes ────────────────────────────────────────────

def _recipe_crashed() -> str:
    return ("# Inspect crash details (driver-specific) :\n"
            "for r in /sys/class/remoteproc/remoteproc*; do\n"
            "  echo \"-- $r\"\n"
            "  cat \"$r\"/{state,name,firmware,crash_count}\n"
            "done\n"
            "# Force a restart :\n"
            "echo stop  | sudo tee /sys/class/remoteproc/<id>/state\n"
            "echo start | sudo tee /sys/class/remoteproc/<id>/state\n")


def _recipe_recovery_off() -> str:
    return ("# Re-enable auto-recovery so the next crash is\n"
            "# handled :\n"
            "echo enabled | sudo tee \\\n"
            "  /sys/class/remoteproc/<id>/recovery\n")


def _recipe_fw_missing() -> str:
    return ("# Firmware blob is missing or unreadable.\n"
            "ls -l /lib/firmware/ | grep -F \"$(cat \\\n"
            "  /sys/class/remoteproc/<id>/firmware)\"\n"
            "# Install vendor firmware package :\n"
            "sudo apt install firmware-misc-nonfree  # Debian\n"
            "# After install, start the coprocessor :\n"
            "echo start | sudo tee /sys/class/remoteproc/<id>/state\n")


def _recipe_offline() -> str:
    return ("# Coprocessor is offline but no firmware metadata.\n"
            "# Try a manual start :\n"
            "echo start | sudo tee /sys/class/remoteproc/<id>/state\n"
            "# Inspect dmesg for the driver's complaint :\n"
            "sudo dmesg | grep -iE 'remoteproc|rproc' | tail\n")
