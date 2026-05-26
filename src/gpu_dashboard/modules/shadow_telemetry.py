"""F3 — Shadow telemetry.

Cross-checks nvidia-smi's power_draw against an external power
meter (Shelly Plug/Pro via local HTTP RPC) and the case ambient
temperature against a 1-wire DS18B20 thermistor (via sysfs w1).

Why this matters:
  - nvidia-smi power_draw is *sampled*, not continuous. arXiv
    2312.02741 shows it captures only ~25% of runtime on
    A100/H100 inference workloads — peaks are missed entirely.
  - External wall meters integrate over time and don't lie about
    PSU loss, fan power, motherboard idle, or anything off-card.
  - In-case ambient is a leading indicator of fan curves
    misbehaving and of thermal-throttle headroom — much more
    actionable than the GPU die temperature alone.

Niche check: there is no other OSS dashboard that reconciles
NVML/nvidia-smi power against an external Shelly meter for the
homelab single-GPU LLM case.

Config keys (config.env):
  SHADOW_SHELLY_URL          http://host[:port]  (Shelly Gen2+ RPC)
  SHADOW_SHELLY_SWITCH_ID    integer, default 0 (which output to read)
  SHADOW_SHELLY_AUTH         optional "user:pass"
  SHADOW_W1_DEVICE           full sysfs path or 28-xxx device id
"""
from __future__ import annotations

import base64
import glob
import json
import os
import socket
import urllib.error
import urllib.parse
import urllib.request
from typing import Any, Dict, Optional


_DEFAULT_TIMEOUT = 1.5  # seconds; we sample on every API call


# -------------------------------------------------------------------- shelly


def _shelly_request(base_url: str, path: str,
                     auth: Optional[str] = None,
                     timeout: float = _DEFAULT_TIMEOUT) -> Dict[str, Any]:
    """GET against a Shelly Gen2+ HTTP RPC endpoint. Returns the
    parsed JSON or raises."""
    url = base_url.rstrip("/") + path
    req = urllib.request.Request(url)
    if auth:
        token = base64.b64encode(auth.encode()).decode("ascii")
        req.add_header("Authorization", "Basic " + token)
    with urllib.request.urlopen(req, timeout=timeout) as r:
        body = r.read().decode("utf-8", "replace")
    return json.loads(body)


def read_shelly(cfg) -> Dict[str, Any]:
    """Sample power/voltage from the configured Shelly Gen2+ device.

    Returns {available: True, power_w, voltage_v, current_a, url}
    on success, or {available: False, reason} when not configured
    or unreachable."""
    url = (cfg.get("SHADOW_SHELLY_URL", "") or "").strip()
    if not url:
        return {"available": False, "reason": "not_configured"}
    switch_id = cfg.get_int("SHADOW_SHELLY_SWITCH_ID", 0)
    auth = (cfg.get("SHADOW_SHELLY_AUTH", "") or "").strip() or None
    try:
        # Shelly Gen2+ RPC over HTTP GET. /rpc/Switch.GetStatus?id=N
        # returns {apower, voltage, current, temperature{tC,tF}, ...}
        data = _shelly_request(
            url, f"/rpc/Switch.GetStatus?id={switch_id}",
            auth=auth)
    except (urllib.error.URLError, socket.timeout,
             ValueError, OSError) as e:
        return {"available": False,
                 "reason": f"unreachable: {type(e).__name__}: {e}"}
    power = data.get("apower")
    voltage = data.get("voltage")
    current = data.get("current")
    if power is None and "meters" in data:
        # Gen1 fallback shape — try /status then meters[0].power
        try:
            g1 = _shelly_request(url, "/status", auth=auth)
            meters = g1.get("meters") or []
            if meters:
                power = meters[0].get("power")
        except (urllib.error.URLError, OSError, ValueError):
            pass
    return {
        "available": True,
        "url": url,
        "switch_id": switch_id,
        "power_w": power,
        "voltage_v": voltage,
        "current_a": current,
        "device_temp_c": (data.get("temperature") or {}).get("tC"),
    }


# ----------------------------------------------------------------- w1 temp


def _resolve_w1_path(spec: str) -> Optional[str]:
    """Given a config value (full sysfs path, or just `28-XXX...`),
    return the path to the `temperature` file. Returns None if no
    device matches."""
    spec = spec.strip()
    if not spec:
        return None
    # Auto-discover when caller asked for the magic string
    if spec.lower() == "auto":
        candidates = sorted(glob.glob("/sys/bus/w1/devices/28-*/temperature"))
        return candidates[0] if candidates else None
    if os.path.isabs(spec):
        if os.path.isdir(spec):
            return os.path.join(spec, "temperature")
        return spec
    # Treat as a device id like "28-0123456789ab"
    p = f"/sys/bus/w1/devices/{spec}/temperature"
    return p if os.path.exists(p) else None


def read_w1(cfg) -> Dict[str, Any]:
    spec = (cfg.get("SHADOW_W1_DEVICE", "") or "").strip()
    if not spec:
        return {"available": False, "reason": "not_configured"}
    path = _resolve_w1_path(spec)
    if not path or not os.path.exists(path):
        return {"available": False,
                 "reason": f"device_not_found: {spec!r}"}
    try:
        with open(path) as f:
            raw = f.read().strip()
    except OSError as e:
        return {"available": False,
                 "reason": f"read_failed: {e}"}
    # Kernel exposes the temperature in millidegrees Celsius as an
    # integer string (e.g. "38437" => 38.437°C).
    try:
        millic = int(raw)
        temp_c = millic / 1000.0
    except ValueError:
        return {"available": False,
                 "reason": f"parse_failed: {raw!r}"}
    return {
        "available": True,
        "path": path,
        "temp_c": round(temp_c, 2),
    }


# --------------------------------------------------------------- reconcile


def _gpu_total_power_w() -> Optional[float]:
    """Sum NVML power_draw across all GPUs (None if no NVML)."""
    try:
        from . import _nvml
        if not _nvml.init():
            return None
        samples = _nvml.sample_all() or []
        total = 0.0
        any_ok = False
        for s in samples:
            p = s.get("power_w") if isinstance(s, dict) else None
            if isinstance(p, (int, float)):
                total += float(p)
                any_ok = True
        return total if any_ok else None
    except Exception:
        return None


def sample(cfg) -> Dict[str, Any]:
    """Combine all external sources and reconcile against NVML.

    Always returns a dict — `available` describes the overall
    state. Individual sources self-report under their own keys."""
    shelly = read_shelly(cfg)
    w1 = read_w1(cfg)
    gpu_total = _gpu_total_power_w()

    delta = None
    if shelly.get("available") and isinstance(
        shelly.get("power_w"), (int, float)
    ) and gpu_total is not None:
        wall = float(shelly["power_w"])
        gpu = float(gpu_total)
        d = wall - gpu
        delta = {
            "wall_w": round(wall, 2),
            "gpu_total_w": round(gpu, 2),
            "non_gpu_w": round(d, 2),
            "non_gpu_pct": round((d / wall * 100), 2) if wall > 0 else None,
        }

    return {
        "available": bool(shelly.get("available")
                            or w1.get("available")),
        "shelly": shelly,
        "w1": w1,
        "nvml": {"gpu_total_power_w": gpu_total},
        "delta": delta,
    }
