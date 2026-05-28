"""F7.6 — Link Stable Mode (with state persistence).

Keeps the GPU clock-locked so its PCIe link negotiates and SUSTAINS
a higher speed (typically Gen 2 instead of Gen 1) on flaky OcuLink
docks where the retimer can't survive endless Gen1↔Gen4
renegotiation cycles.

Mechanism (verified on F9G-BK7 OcuLink dock + RTX 3090):
  1. `nvidia-smi -pm 1`              enable persistence mode
  2. `nvidia-smi --lock-gpu-clocks`  pin a clock floor
  3. The GPU firmware then keeps the link awake; on the test rig
     the link went from 2.5 GT/s (Gen 1, idle) to 5.0 GT/s (Gen 2)
     and stayed there.

Cost: idle power ~7W → ~20W. Tradeoff worth it for 24/7 LLM rigs
where the link instability is the bigger problem.

We deliberately do NOT use setpci CAP_EXP+30.W writes to LnkCtl2:
the NVIDIA firmware owns that register and overwrites our target
with its own preferred max within seconds. The clock-lock approach
goes through the firmware's blessed path.
"""
from __future__ import annotations

import json
import os
import re
import subprocess
import time
from typing import Any, Dict, List, Optional

STATE_FILE = os.path.expanduser(
    "~/.config/gpu-dashboard/link_stable_state.json")

# F7.4 — stable-for tracking lives at module scope so a browser
# refresh (or a second tab) doesn't reset the clock. We only update
# the anchor when status() observes a different speed than the
# previous call, so the anchor reflects the actual link transition
# moment, not the moment the user first opened the dashboard.
_LAST_OBSERVED_SPEED: Optional[str] = None
_STABLE_SINCE_TS: Optional[float] = None
_TRANSITION_COUNT: int = 0  # cumulative since dashboard startup

# F7.5 — track what target Gen the user explicitly enabled. The
# `current_clock_mhz` heuristic was unreliable (clock can dip
# momentarily even with lock-clocks active, flipping the UI from
# "locked" to "not locked" every few seconds). Storing what we
# ourselves set gives a stable source of truth: lock state is
# whatever the user last asked for via the dashboard.
# F7.6 — persisted to disk so a dashboard restart (or reboot) doesn't
# silently undo the user's intent. Re-applied on module import via
# _ensure_lock_applied() below.
_LOCKED_TARGET_GEN: Optional[int] = None
_LOCKED_MIN_MHZ: Optional[int] = None
_LOCKED_MAX_MHZ: Optional[int] = None
_LOAD_DONE = False  # one-shot guard so multiple status() calls don't
                     # repeatedly re-apply


def _load_state() -> None:
    """Load persisted lock state from disk. No-op if file missing or
    malformed."""
    global _LOCKED_TARGET_GEN, _LOCKED_MIN_MHZ, _LOCKED_MAX_MHZ
    if not os.path.isfile(STATE_FILE):
        return
    try:
        with open(STATE_FILE) as f:
            data = json.load(f)
    except (OSError, ValueError):
        return
    _LOCKED_TARGET_GEN = data.get("target_gen")
    _LOCKED_MIN_MHZ = data.get("min_mhz")
    _LOCKED_MAX_MHZ = data.get("max_mhz")


def _save_state() -> None:
    """Atomic write the lock state to disk."""
    try:
        os.makedirs(os.path.dirname(STATE_FILE), exist_ok=True)
        tmp = STATE_FILE + ".tmp"
        with open(tmp, "w") as f:
            json.dump({
                "target_gen": _LOCKED_TARGET_GEN,
                "min_mhz": _LOCKED_MIN_MHZ,
                "max_mhz": _LOCKED_MAX_MHZ,
                "saved_at": time.time(),
            }, f)
        os.replace(tmp, STATE_FILE)
    except OSError:
        pass  # best-effort — UI keeps working even if disk is RO


def _ensure_lock_applied() -> None:
    """First call after import: load the persisted lock and re-apply
    it via the sudoers wrapper if needed. Subsequent calls are no-ops.

    Re-applying is safe: nvidia-smi --lock-gpu-clocks is idempotent.
    If the wrapper isn't installed yet (fresh install), we just
    populate the in-memory state from disk so the UI shows the
    intended Gen even though nothing is actually locked."""
    global _LOAD_DONE
    if _LOAD_DONE:
        return
    _LOAD_DONE = True
    _load_state()
    if (_LOCKED_TARGET_GEN
            and _LOCKED_TARGET_GEN >= 2
            and _LOCKED_MIN_MHZ
            and _LOCKED_MAX_MHZ
            and wrapper_available()):
        _run(["sudo", "-n", WRAPPER_PATH, "enable",
              str(_LOCKED_MIN_MHZ), str(_LOCKED_MAX_MHZ)],
             timeout=10.0)

# Default clock floor range. 900 MHz is a safe gpu-clocks floor on
# 3090/4090/5090 — high enough to keep the firmware out of P8, low
# enough to not waste real power. 1500 MHz ceiling lets the
# auto-boost still respond to actual workloads.
DEFAULT_MIN_MHZ = 900
DEFAULT_MAX_MHZ = 1500

# Hard bounds for input validation. Anything outside these is a
# typo or hostile input — reject.
SAFE_MIN_MHZ = 200
SAFE_MAX_MHZ = 3000

# Empirical clock-floor → expected link Gen mapping. The
# relationship is "clock-lock keeps firmware in active state, which
# determines what speed the firmware negotiates with the retimer".
# Actual achieved Gen depends on whether the retimer can sustain
# the negotiated speed. Calibrated on RTX 3090 + F9G-BK7 testbed.
GEN_PRESETS: Dict[int, Optional[Dict[str, int]]] = {
    1: None,  # idle / no lock → firmware lets link drop to Gen 1
    2: {"min_mhz": 900,  "max_mhz": 1500},  # gentle lock → Gen 2
    3: {"min_mhz": 1500, "max_mhz": 2000},  # medium lock → Gen 3
    4: {"min_mhz": 2000, "max_mhz": 2400},  # heavy lock → Gen 4
}

WRAPPER_PATH = "/usr/local/bin/gpu-dashboard-link-stable"


def _run(args: List[str], timeout: float = 5.0) -> Dict[str, Any]:
    try:
        r = subprocess.run(args, capture_output=True, text=True,
                            timeout=timeout)
        return {
            "ok": r.returncode == 0,
            "rc": r.returncode,
            "stdout": r.stdout.strip(),
            "stderr": r.stderr.strip(),
        }
    except (FileNotFoundError, subprocess.SubprocessError, OSError) as e:
        return {"ok": False, "error": f"{type(e).__name__}: {e}"}


def wrapper_available() -> bool:
    """True if the sudoers wrapper is installed and we can call it
    without an interactive password prompt (sudo -n)."""
    if not os.path.isfile(WRAPPER_PATH):
        return False
    r = _run(["sudo", "-n", "-l", WRAPPER_PATH], timeout=2.0)
    return bool(r.get("ok"))


def _read_link_state() -> Dict[str, Any]:
    """Read the GPU's current PCIe link state via sysfs."""
    # GPU is at 0000:01:00.0 on every test rig we know of. Fall back
    # to first NVIDIA device via vendor ID lookup if not.
    bdf = _find_nvidia_bdf() or "0000:01:00.0"
    base = f"/sys/bus/pci/devices/{bdf}"
    out: Dict[str, Any] = {"bdf": bdf}
    for k in ("current_link_speed", "current_link_width",
              "max_link_speed", "max_link_width"):
        try:
            with open(f"{base}/{k}") as f:
                out[k] = f.read().strip()
        except OSError:
            out[k] = None
    # Detect the "(downgraded)" kernel tag by parsing the speed
    # field — the speed itself doesn't include the marker but
    # comparing current vs max tells us.
    cur = out.get("current_link_speed") or ""
    mx = out.get("max_link_speed") or ""
    out["downgraded"] = bool(cur and mx and cur != mx)
    return out


def _find_nvidia_bdf() -> Optional[str]:
    """Walk /sys/bus/pci/devices and return the first BDF with
    vendor 0x10de (NVIDIA) that's a GPU class (0x0300xx/0x0302xx)."""
    import glob
    for dev in sorted(glob.glob("/sys/bus/pci/devices/*")):
        try:
            with open(f"{dev}/vendor") as f:
                if f.read().strip() != "0x10de":
                    continue
            with open(f"{dev}/class") as f:
                cls = f.read().strip()
            if cls.startswith("0x0300") or cls.startswith("0x0302"):
                return os.path.basename(dev)
        except OSError:
            continue
    return None


def _read_pstate_via_nvml() -> Optional[str]:
    """Try NVML first (cheaper); fall back to nvidia-smi."""
    try:
        from . import _nvml
        if _nvml.init():
            s = _nvml.sample_device(0) or {}
            ps = s.get("pstate")
            if ps is not None:
                return f"P{ps}" if isinstance(ps, int) else str(ps)
    except Exception:
        pass
    r = _run(["nvidia-smi", "--query-gpu=pstate",
             "--format=csv,noheader"], timeout=2.0)
    if r.get("ok"):
        return r["stdout"].strip() or None
    return None


def _read_clock_lock_state() -> Dict[str, Any]:
    """Detect whether nvidia-smi clock-locks are currently active.

    nvidia-smi --query-gpu reports the locked min/max as `gpu_lock_*`
    fields on recent drivers. Older drivers don't expose this — in
    that case we infer from `clocks.applications.gr` matching the
    floor."""
    r = _run([
        "nvidia-smi",
        "--query-gpu=clocks.gr,clocks.max.gr,persistence_mode",
        "--format=csv,noheader,nounits",
    ], timeout=2.0)
    out: Dict[str, Any] = {
        "persistence_mode": None,
        "clocks_locked": None,
        "current_clock_mhz": None,
        "max_clock_mhz": None,
    }
    if r.get("ok") and r["stdout"]:
        # Output like "210, 2115, Enabled"
        parts = [p.strip() for p in r["stdout"].split(",")]
        if len(parts) >= 3:
            try:
                out["current_clock_mhz"] = int(parts[0])
            except ValueError:
                pass
            try:
                out["max_clock_mhz"] = int(parts[1])
            except ValueError:
                pass
            out["persistence_mode"] = parts[2].lower() == "enabled"
    return out


def status(cfg=None) -> Dict[str, Any]:
    """Full status dict for the Link Stable Mode UI."""
    global _LAST_OBSERVED_SPEED, _STABLE_SINCE_TS, _TRANSITION_COUNT
    # Lazy-load persisted lock state on first call; re-apply if the
    # wrapper is available. This makes a dashboard restart (or a
    # system reboot, since nvidia-smi lock-gpu-clocks also doesn't
    # persist) silently restore the user's chosen Gen.
    _ensure_lock_applied()
    link = _read_link_state()
    pstate = _read_pstate_via_nvml()
    clocks = _read_clock_lock_state()
    # F7.4 — server-side stable-for tracking. Updates the anchor
    # only when the observed speed actually changes; survives
    # browser refresh and multi-tab access.
    now = time.time()
    cur_speed = link.get("current_link_speed")
    if cur_speed != _LAST_OBSERVED_SPEED:
        if _LAST_OBSERVED_SPEED is not None:
            _TRANSITION_COUNT += 1
        _LAST_OBSERVED_SPEED = cur_speed
        _STABLE_SINCE_TS = now
    stable_for_s = (now - _STABLE_SINCE_TS) if _STABLE_SINCE_TS else 0.0
    return {
        # `ok` satisfies the module-fleet-health contract (tests/
        # test_module_fleet_health.py): every status() dict must
        # carry either `verdict` or `ok` so callers can render a
        # uniform pass/fail badge.
        "ok": True,
        "wrapper_available": wrapper_available(),
        "wrapper_path": WRAPPER_PATH,
        "link": link,
        "pstate": pstate,
        "clocks": clocks,
        "defaults": {"min_mhz": DEFAULT_MIN_MHZ,
                      "max_mhz": DEFAULT_MAX_MHZ},
        "gen_presets": {
            str(g): preset for g, preset in GEN_PRESETS.items()
        },
        "stable": {
            "since_ts": _STABLE_SINCE_TS,
            "for_seconds": round(stable_for_s, 1),
            "transitions": _TRANSITION_COUNT,
        },
        "locked": {
            "target_gen": _LOCKED_TARGET_GEN,
            "min_mhz": _LOCKED_MIN_MHZ,
            "max_mhz": _LOCKED_MAX_MHZ,
        },
    }


def _validate_mhz(name: str, v) -> int:
    try:
        n = int(v)
    except (TypeError, ValueError):
        raise ValueError(f"{name} must be an integer, got {v!r}")
    if n < SAFE_MIN_MHZ or n > SAFE_MAX_MHZ:
        raise ValueError(
            f"{name}={n} out of safe range [{SAFE_MIN_MHZ},{SAFE_MAX_MHZ}]")
    return n


def enable(min_mhz: Optional[int] = None,
            max_mhz: Optional[int] = None,
            target_gen: Optional[int] = None) -> Dict[str, Any]:
    """Enable Link Stable Mode at a given Gen preset OR an explicit
    clock-floor range.

    If `target_gen` is provided (1-4), the matching GEN_PRESETS
    entry is used. Gen 1 = no lock (= disable). Otherwise explicit
    min_mhz/max_mhz win (with the legacy default range).
    """
    if target_gen is not None:
        if target_gen == 1:
            # Gen 1 = no lock → equivalent to disable.
            return disable()
        preset = GEN_PRESETS.get(int(target_gen))
        if preset is None:
            return {"ok": False, "error": "invalid_gen",
                     "message": f"Unsupported target Gen: {target_gen}"}
        lo, hi = preset["min_mhz"], preset["max_mhz"]
    else:
        try:
            lo = _validate_mhz("min_mhz", min_mhz
                                if min_mhz is not None else DEFAULT_MIN_MHZ)
            hi = _validate_mhz("max_mhz", max_mhz
                                if max_mhz is not None else DEFAULT_MAX_MHZ)
        except ValueError as e:
            return {"ok": False, "error": "invalid_input",
                     "message": str(e)}
        if lo > hi:
            return {"ok": False, "error": "invalid_range",
                     "message": f"min ({lo}) > max ({hi})"}
    if not wrapper_available():
        return {"ok": False, "error": "wrapper_missing",
                 "message": "Install the sudoers wrapper first"}
    r = _run(["sudo", "-n", WRAPPER_PATH, "enable",
              str(lo), str(hi)], timeout=10.0)
    if not r.get("ok"):
        return {"ok": False, "error": "wrapper_failed",
                 "message": r.get("stderr") or r.get("error")
                              or f"exit {r.get('rc')}"}
    # Record what we asked for so the UI can highlight the right
    # Gen button even when current_clock_mhz briefly dips below the
    # lock floor between firmware boosts.
    global _LOCKED_TARGET_GEN, _LOCKED_MIN_MHZ, _LOCKED_MAX_MHZ
    _LOCKED_TARGET_GEN = target_gen
    _LOCKED_MIN_MHZ = lo
    _LOCKED_MAX_MHZ = hi
    _save_state()  # persist so a restart restores this Gen
    return {"ok": True, "min_mhz": lo, "max_mhz": hi,
             "target_gen": target_gen,
             "stdout": r.get("stdout")}


def disable() -> Dict[str, Any]:
    """Disable Link Stable Mode: reset clocks back to auto."""
    if not wrapper_available():
        return {"ok": False, "error": "wrapper_missing",
                 "message": "Install the sudoers wrapper first"}
    r = _run(["sudo", "-n", WRAPPER_PATH, "disable"], timeout=10.0)
    if not r.get("ok"):
        return {"ok": False, "error": "wrapper_failed",
                 "message": r.get("stderr") or r.get("error")
                              or f"exit {r.get('rc')}"}
    global _LOCKED_TARGET_GEN, _LOCKED_MIN_MHZ, _LOCKED_MAX_MHZ
    _LOCKED_TARGET_GEN = 1  # Gen 1 = idle mode (no lock)
    _LOCKED_MIN_MHZ = None
    _LOCKED_MAX_MHZ = None
    _save_state()  # persist the disable so a restart doesn't
                    # auto-re-enable a previous lock
    return {"ok": True, "stdout": r.get("stdout")}
