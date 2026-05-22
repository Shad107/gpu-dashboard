"""Module wall_meter — read true PSU/wall consumption from smart plugs (R&D #12.1).

nvidia-smi reports GPU power draw only. The actual wall socket draw is
typically 1.1×-1.3× higher because of PSU inefficiency + CPU + fans +
memory + chipset. Without measuring the wall, energy cost calculations
are systematically under-estimated.

This module polls a smart-plug HTTP endpoint :
  - **Shelly Gen1**  : http://<ip>/status                 (JSON, .meters[0].power)
  - **Shelly Gen2/Plus** : http://<ip>/rpc/Switch.GetStatus?id=0  (JSON, .apower)
  - **Tasmota**      : http://<ip>/cm?cmnd=Status%208     (JSON, .StatusSNS.ENERGY.Power)

All probes use stdlib urllib + 2s timeout. No external deps.

Config (in config.env) :
  WALL_METER_URL   = http://shelly.local             # plug's HTTP base
  WALL_METER_KIND  = shelly1 | shelly_plus | tasmota  # adapter selector
  WALL_METER_BASELINE_W = 35   # background draw when GPU is idle (CPU+motherboard)
                                # used to compute PSU efficiency
"""
from __future__ import annotations

import json
import urllib.error
import urllib.request
from typing import Optional


NAME = "wall_meter"

_TIMEOUT = 2.0


def _http_json(url: str) -> Optional[dict]:
    try:
        with urllib.request.urlopen(url, timeout=_TIMEOUT) as r:
            if r.status != 200:
                return None
            return json.loads(r.read().decode("utf-8"))
    except (urllib.error.URLError, urllib.error.HTTPError, OSError,
            json.JSONDecodeError, TimeoutError):
        return None


def _probe_shelly1(base_url: str) -> Optional[float]:
    """Shelly Gen1 : GET /status → {meters: [{power: 123.4, ...}, ...]}"""
    d = _http_json(f"{base_url.rstrip('/')}/status")
    if not d or not isinstance(d.get("meters"), list) or not d["meters"]:
        return None
    p = d["meters"][0].get("power")
    return float(p) if p is not None else None


def _probe_shelly_plus(base_url: str) -> Optional[float]:
    """Shelly Gen2 / Plus : GET /rpc/Switch.GetStatus?id=0 → {apower: 123.4, ...}"""
    d = _http_json(f"{base_url.rstrip('/')}/rpc/Switch.GetStatus?id=0")
    if not d:
        return None
    p = d.get("apower")
    return float(p) if p is not None else None


def _probe_tasmota(base_url: str) -> Optional[float]:
    """Tasmota : GET /cm?cmnd=Status%208 → {StatusSNS: {ENERGY: {Power: 123.4, ...}}}"""
    d = _http_json(f"{base_url.rstrip('/')}/cm?cmnd=Status%208")
    if not d:
        return None
    energy = d.get("StatusSNS", {}).get("ENERGY", {}) if isinstance(d, dict) else {}
    p = energy.get("Power")
    return float(p) if p is not None else None


_ADAPTERS = {
    "shelly1": _probe_shelly1,
    "shelly_plus": _probe_shelly_plus,
    "tasmota": _probe_tasmota,
}


def probe(kind: str, url: str) -> Optional[float]:
    """Generic dispatch by adapter kind. Returns watts or None on failure."""
    fn = _ADAPTERS.get(kind)
    if fn is None:
        return None
    return fn(url)


def kinds_supported() -> list:
    return list(_ADAPTERS.keys())


def efficiency(gpu_w: float, wall_w: float, baseline_w: float) -> Optional[float]:
    """GPU power as a fraction of (wall - baseline). Returns None if denominator
    is non-positive (baseline too high relative to wall — usually idle and we
    can't divide meaningfully)."""
    headroom = wall_w - baseline_w
    if headroom <= 0:
        return None
    return min(1.0, max(0.0, gpu_w / headroom))


def status(cfg, gpu_w: Optional[float] = None) -> dict:
    """Read configured wall-meter + return aggregated reading.

    Returns :
      {ok, available, kind, url, wall_w, baseline_w, headroom_w, gpu_w,
       psu_efficiency_pct, error?}
    """
    url = cfg.get("WALL_METER_URL", "") or ""
    kind = cfg.get("WALL_METER_KIND", "shelly1") or "shelly1"
    try:
        baseline_w = float(cfg.get("WALL_METER_BASELINE_W", "35") or "35")
    except (ValueError, TypeError):
        baseline_w = 35.0

    if not url:
        return {"ok": True, "available": False, "reason": "no WALL_METER_URL configured"}
    if kind not in _ADAPTERS:
        return {"ok": True, "available": False,
                "reason": f"unknown kind {kind!r} ; supported: {kinds_supported()}"}

    wall_w = probe(kind, url)
    if wall_w is None:
        return {"ok": True, "available": False, "kind": kind, "url": url,
                "reason": "probe failed (meter unreachable / bad response)"}

    out = {
        "ok": True, "available": True,
        "kind": kind, "url": url,
        "wall_w": round(wall_w, 1),
        "baseline_w": round(baseline_w, 1),
        "headroom_w": round(max(0, wall_w - baseline_w), 1),
        "gpu_w": round(gpu_w, 1) if gpu_w is not None else None,
    }
    if gpu_w is not None:
        eff = efficiency(gpu_w, wall_w, baseline_w)
        out["psu_efficiency_pct"] = round(eff * 100, 1) if eff is not None else None
    return out
