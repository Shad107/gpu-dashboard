"""Module tdp_auto — automatic TDP profile switcher (R&D #17.3).

Classifies the rolling util window into one of {idle, light, heavy} states
and selects an appropriate power-limit profile. Saves idle watts by
running at a lower cap when nothing's training, restores full TDP for
inference bursts. Hysteresis prevents flapping.

Config at ~/.config/gpu-dashboard/tdp_auto.json :
  {
    "enabled": true,
    "window_s": 60,
    "hysteresis_s": 30,
    "thresholds": {"idle_max_util": 5, "heavy_min_util": 70},
    "profiles": {
      "idle":  {"power_limit_w": 100, "gpu_offset_mhz": -100, "mem_offset_mhz": 0},
      "light": {"power_limit_w": 200, "gpu_offset_mhz": 0,    "mem_offset_mhz": 0},
      "heavy": {"power_limit_w": 350, "gpu_offset_mhz": 100,  "mem_offset_mhz": 1000}
    }
  }

Decision logic :
  - mean util over last `window_s` seconds
  - util <= idle_max_util          → idle profile
  - util >= heavy_min_util         → heavy profile
  - otherwise                      → light profile
  - new state must persist for >= hysteresis_s before applying

The module is pure logic + nvidia-smi (or pl module) subprocess. No
external deps.
"""
from __future__ import annotations

import json
import os
import time
from typing import Optional


NAME = "tdp_auto"

_CFG_PATH = "~/.config/gpu-dashboard/tdp_auto.json"
_STATE_PATH = "~/.config/gpu-dashboard/tdp_auto_state.json"

_DEFAULT_CFG = {
    "enabled": False,
    "window_s": 60,
    "hysteresis_s": 30,
    "thresholds": {"idle_max_util": 5, "heavy_min_util": 70},
    "profiles": {
        "idle":  {"power_limit_w": 100, "gpu_offset_mhz": -100, "mem_offset_mhz": 0},
        "light": {"power_limit_w": 200, "gpu_offset_mhz": 0,    "mem_offset_mhz": 0},
        "heavy": {"power_limit_w": 350, "gpu_offset_mhz": 100,  "mem_offset_mhz": 1000},
    },
}


def cfg_path() -> str:
    return os.path.expanduser(_CFG_PATH)


def state_path() -> str:
    return os.path.expanduser(_STATE_PATH)


def load_config() -> dict:
    p = cfg_path()
    if not os.path.exists(p):
        return dict(_DEFAULT_CFG)
    try:
        with open(p) as f:
            d = json.load(f)
        if isinstance(d, dict):
            # Merge with defaults to fill missing keys
            merged = dict(_DEFAULT_CFG)
            merged.update(d)
            return merged
    except (OSError, json.JSONDecodeError):
        pass
    return dict(_DEFAULT_CFG)


def save_config(cfg: dict) -> None:
    p = cfg_path()
    os.makedirs(os.path.dirname(p), exist_ok=True)
    with open(p, "w") as f:
        json.dump(cfg, f, indent=2)


def load_state() -> dict:
    p = state_path()
    if not os.path.exists(p):
        return {"current_profile": "light", "since_ts": 0, "history": []}
    try:
        with open(p) as f:
            d = json.load(f)
        return d if isinstance(d, dict) else {"current_profile": "light", "since_ts": 0, "history": []}
    except (OSError, json.JSONDecodeError):
        return {"current_profile": "light", "since_ts": 0, "history": []}


def save_state(state: dict) -> None:
    p = state_path()
    os.makedirs(os.path.dirname(p), exist_ok=True)
    state["history"] = state.get("history", [])[-100:]   # cap
    with open(p, "w") as f:
        json.dump(state, f, indent=2)


def classify(samples: list, window_s: int,
             idle_max_util: float, heavy_min_util: float,
             now_ts: Optional[float] = None) -> str:
    """Return 'idle' / 'light' / 'heavy' for the given samples.

    Each sample is a dict with at least `ts` (epoch seconds) and
    `util_gpu`. samples outside [now - window_s, now] are ignored.
    """
    if not samples:
        return "light"
    if now_ts is None:
        # Use the newest sample's ts as 'now'
        latest = samples[-1].get("ts")
        try:
            now_ts = float(latest) if latest is not None else time.time()
        except (ValueError, TypeError):
            now_ts = time.time()
    cutoff = now_ts - window_s
    util_values: list = []
    for s in samples:
        ts_raw = s.get("ts")
        try:
            ts = float(ts_raw)
        except (ValueError, TypeError):
            continue
        if ts < cutoff:
            continue
        util = s.get("util_gpu")
        if util is None:
            continue
        try:
            util_values.append(float(util))
        except (ValueError, TypeError):
            continue
    if not util_values:
        return "light"
    mean = sum(util_values) / len(util_values)
    if mean <= idle_max_util:
        return "idle"
    if mean >= heavy_min_util:
        return "heavy"
    return "light"


def decide_switch(samples: list, cfg: dict,
                  prev_state: dict,
                  now_ts: Optional[float] = None) -> dict:
    """Decide whether to switch profile, applying hysteresis.

    Returns :
      {target_profile, current_profile, would_switch: bool,
       reason: 'unchanged' | 'hysteresis-pending' | 'switch',
       elapsed_in_target_s, mean_util}

    When now_ts is omitted, defaults to the latest sample's ts (matches
    classify()'s convention so callers can pass in offline sample sets
    without computing time.time() themselves).
    """
    if now_ts is None:
        if samples:
            latest = samples[-1].get("ts")
            try:
                now_ts = float(latest) if latest is not None else time.time()
            except (ValueError, TypeError):
                now_ts = time.time()
        else:
            now_ts = time.time()
    window_s = int(cfg.get("window_s", 60))
    hyst_s = int(cfg.get("hysteresis_s", 30))
    thr = cfg.get("thresholds", {})
    idle_max = float(thr.get("idle_max_util", 5))
    heavy_min = float(thr.get("heavy_min_util", 70))

    target = classify(samples, window_s, idle_max, heavy_min, now_ts=now_ts)
    current = prev_state.get("current_profile", "light")

    # Compute mean util for context
    cutoff = now_ts - window_s
    utils = [float(s.get("util_gpu") or 0)
             for s in samples
             if isinstance(s.get("ts"), (int, float)) and float(s["ts"]) >= cutoff
             and s.get("util_gpu") is not None]
    mean_util = sum(utils) / len(utils) if utils else 0

    if target == current:
        return {
            "target_profile": target, "current_profile": current,
            "would_switch": False, "reason": "unchanged",
            "mean_util": round(mean_util, 1),
        }

    # Hysteresis : we must have been in the new target for >= hysteresis_s
    pending_since = prev_state.get("pending_target_since_ts")
    pending_target = prev_state.get("pending_target")
    if pending_target != target:
        # New target observed — reset the hysteresis timer
        return {
            "target_profile": target, "current_profile": current,
            "would_switch": False, "reason": "hysteresis-pending",
            "elapsed_in_target_s": 0, "mean_util": round(mean_util, 1),
        }
    elapsed = now_ts - float(pending_since)
    if elapsed < hyst_s:
        return {
            "target_profile": target, "current_profile": current,
            "would_switch": False, "reason": "hysteresis-pending",
            "elapsed_in_target_s": int(elapsed),
            "hysteresis_s": hyst_s,
            "mean_util": round(mean_util, 1),
        }
    return {
        "target_profile": target, "current_profile": current,
        "would_switch": True, "reason": "switch",
        "elapsed_in_target_s": int(elapsed),
        "mean_util": round(mean_util, 1),
    }


def evaluate(samples: list, dry_run: bool = True) -> dict:
    """Top-level entry. Decides whether to switch + (optionally) applies."""
    cfg = load_config()
    state = load_state()
    now = time.time()
    decision = decide_switch(samples, cfg, state, now_ts=now)

    # Update pending-target tracking
    target = decision["target_profile"]
    if target != state.get("current_profile") and target != state.get("pending_target"):
        state["pending_target"] = target
        state["pending_target_since_ts"] = now
    elif target == state.get("current_profile"):
        state.pop("pending_target", None)
        state.pop("pending_target_since_ts", None)

    if decision["would_switch"] and cfg.get("enabled") and not dry_run:
        # Apply via the existing power_limit module if available
        profile = cfg["profiles"].get(target, {})
        applied = _apply_profile(profile)
        state["current_profile"] = target
        state["since_ts"] = int(now)
        state.pop("pending_target", None)
        state.pop("pending_target_since_ts", None)
        state.setdefault("history", []).append({
            "ts": int(now), "from": decision["current_profile"], "to": target,
            "applied": applied, "mean_util": decision.get("mean_util"),
        })
        decision["applied"] = applied

    save_state(state)
    return {"ok": True, "decision": decision, "config_enabled": cfg.get("enabled", False),
            "dry_run": dry_run}


def _apply_profile(profile: dict) -> dict:
    """Best-effort apply of a profile via nvidia-smi -pl. Returns
    {power_limit: ok|err, gpu_offset: deferred, ...}."""
    import subprocess
    out: dict = {}
    pl = profile.get("power_limit_w")
    if pl is not None:
        try:
            r = subprocess.run(
                ["nvidia-smi", "-i", "0", "-pl", str(int(pl))],
                capture_output=True, text=True, timeout=4,
            )
            out["power_limit_w"] = {"ok": r.returncode == 0,
                                     "msg": (r.stderr or r.stdout).strip()[:80]}
        except (FileNotFoundError, subprocess.SubprocessError, OSError) as e:
            out["power_limit_w"] = {"ok": False, "msg": str(e)[:80]}
    if profile.get("gpu_offset_mhz") is not None:
        out["gpu_offset_mhz"] = {"deferred": True, "value": profile["gpu_offset_mhz"]}
    if profile.get("mem_offset_mhz") is not None:
        out["mem_offset_mhz"] = {"deferred": True, "value": profile["mem_offset_mhz"]}
    return out


def dry_run_preview(samples_history: list, window_s: int = 3600) -> dict:
    """Simulate evaluate() over a window of historical samples.
    Returns hypothetical switches that would have occurred."""
    cfg = load_config()
    if not samples_history:
        return {"ok": True, "switches": [], "reason": "no samples"}
    # Sort by ts ascending
    sorted_samples = sorted(samples_history, key=lambda s: s.get("ts", 0))
    if not sorted_samples:
        return {"ok": True, "switches": [], "reason": "no valid samples"}
    last_ts = sorted_samples[-1].get("ts")
    try:
        last_ts = float(last_ts)
    except (ValueError, TypeError):
        return {"ok": True, "switches": [], "reason": "invalid ts"}
    cutoff = last_ts - window_s
    window_samples = [s for s in sorted_samples
                       if isinstance(s.get("ts"), (int, float)) and float(s["ts"]) >= cutoff]
    # Simulate state-machine evolution
    sim_state = {"current_profile": "light", "since_ts": 0}
    switches: list = []
    win_seconds = int(cfg.get("window_s", 60))
    # Walk through samples in 30s steps (cheap)
    step_s = 30
    cur_ts = cutoff
    while cur_ts <= last_ts:
        window_slice = [s for s in window_samples
                         if cur_ts - win_seconds <= float(s.get("ts", 0)) <= cur_ts]
        d = decide_switch(window_slice, cfg, sim_state, now_ts=cur_ts)
        if d["target_profile"] != sim_state["current_profile"]:
            if sim_state.get("pending_target") != d["target_profile"]:
                sim_state["pending_target"] = d["target_profile"]
                sim_state["pending_target_since_ts"] = cur_ts
            else:
                elapsed = cur_ts - sim_state["pending_target_since_ts"]
                if elapsed >= cfg.get("hysteresis_s", 30):
                    switches.append({
                        "ts": int(cur_ts),
                        "from": sim_state["current_profile"],
                        "to": d["target_profile"],
                        "mean_util": d.get("mean_util"),
                    })
                    sim_state["current_profile"] = d["target_profile"]
                    sim_state.pop("pending_target", None)
                    sim_state.pop("pending_target_since_ts", None)
        else:
            sim_state.pop("pending_target", None)
            sim_state.pop("pending_target_since_ts", None)
        cur_ts += step_s
    return {
        "ok": True,
        "window_s": window_s,
        "samples_evaluated": len(window_samples),
        "switches": switches,
        "switch_count": len(switches),
    }


def status() -> dict:
    cfg = load_config()
    state = load_state()
    return {
        "ok": True,
        "config": cfg,
        "current_profile": state.get("current_profile", "light"),
        "pending_target": state.get("pending_target"),
        "history": state.get("history", [])[-20:],
    }
