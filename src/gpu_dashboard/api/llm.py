"""HTTP handlers for LLM monitoring + history scrubber.

Extracted from the legacy monolith in cycle 4 of the api/ split.
Covers R&D #4 LLM perf + #8.1 snapshot + #8.4 llama-bench + #8.7 Jupyter.

Helpers like _parse_llamacpp_metrics, _tokens_per_watt and
_llm_model_served move with the LLM handlers since they're scoped here.
"""
from __future__ import annotations

import json
import os
import re
import time
import subprocess
from typing import Optional, Tuple

from .. import detect
from . import _core as _m


Response = Tuple[int, dict]


def _gpu_card_snapshot(gpu_index: int = 0):
    """Forward to _core so tests patching api._core._gpu_card_snapshot
    are honored everywhere."""
    return _m._gpu_card_snapshot(gpu_index)


def _gpus_available():
    return _m._gpus_available()


def _parse_gpu_index(params):
    return _m._parse_gpu_index(params)


def _read_cmdline(pid):
    return _m._read_cmdline(pid)


def _llm_model_served(cfg) -> str:
    """If a local LLM server URL is configured, fetch the model id from /v1/models."""
    url = cfg.get("LLM_SERVER_URL", "")
    if not url:
        return ""
    import urllib.request, json as _json
    try:
        with urllib.request.urlopen(f"{url.rstrip('/')}/v1/models", timeout=2) as r:
            data = _json.loads(r.read().decode())
            models = data.get("data", [])
            if models:
                return models[0].get("id", "?")
    except Exception:
        pass
    return ""


# ────────────────────────── GET /api/llm/lifetime ─────────────────────────


def handle_llm_lifetime(ctx: dict, params: Optional[dict] = None) -> Response:
    """Cumulative LLM stats since the first sample with a tokens count.

    Walks the samples table, sums positive deltas of tokens_total_snapshot
    (negative deltas = llama-server restart, ignored). Returns avg power
    and avg tokens-per-watt over the same window.

    Returns :
      ok: bool
      available: bool         — whether any sample had a tokens count
      since_ts: int | None    — first sample with tokens, in epoch seconds
      latest_snapshot: int    — last seen tokens_total_snapshot
      total_tokens_generated: int  — sum of positive deltas
      restart_count: int      — number of detected counter resets
      avg_power_watts: float
      avg_tokens_per_watt: float | None
    """
    storage = ctx.get("storage")
    if storage is None:
        return 503, {"ok": False, "error": "storage not available"}

    gpu = _parse_gpu_index(params or {})
    samples = storage.get_samples(from_ts=0, gpu_index=gpu)
    import datetime as _dt2
    import time as _t2
    year_start_ts = int(_dt2.datetime.fromtimestamp(_t2.time()).replace(
        month=1, day=1, hour=0, minute=0, second=0, microsecond=0).timestamp())
    total = 0
    total_this_year = 0
    restarts = 0
    prev = None
    first_ts = None
    latest = None
    powers: list = []
    for s in samples:
        tok = s.get("tokens_total_snapshot")
        if tok is None:
            continue
        if first_ts is None:
            first_ts = s["ts"]
        latest = tok
        if prev is not None:
            delta = tok - prev
            if delta > 0:
                total += delta
                if s["ts"] >= year_start_ts:
                    total_this_year += delta
            elif delta < 0:
                restarts += 1
        prev = tok
        if s.get("power"):
            powers.append(s["power"])

    available = first_ts is not None
    avg_power = (sum(powers) / len(powers)) if powers else 0.0

    avg_tpw = None
    if available and latest is not None and powers and total > 0:
        # span : last sample with tokens minus first sample with tokens
        span = max(1, samples[-1]["ts"] - first_ts)
        tps = total / span
        if avg_power > 0:
            avg_tpw = tps / avg_power

    return 200, {
        "ok": True,
        "available": available,
        "since_ts": first_ts,
        "latest_snapshot": latest or 0,
        "total_tokens_generated": total,
        "total_tokens_this_year": total_this_year,
        "year_start_ts": year_start_ts,
        "restart_count": restarts,
        "avg_power_watts": round(avg_power, 2),
        "avg_tokens_per_watt": round(avg_tpw, 4) if avg_tpw else None,
    }


# ────────────────────────── GET /api/llm/perf ─────────────────────────────


def handle_llm_perf(ctx: dict, params: Optional[dict] = None) -> Response:
    """Live + recent tokens-per-second across multiple rolling windows.

    Used by the Stats page sparklines + the LLM card live indicator.
    """
    storage = ctx.get("storage")
    if storage is None:
        return 503, {"ok": False, "error": "storage not available"}

    import time as _time
    now = int(_time.time())

    gpu = _parse_gpu_index(params or {})
    samples = storage.get_samples(from_ts=now - 86400, to_ts=now, gpu_index=gpu)
    token_samples = [s for s in samples if s.get("tokens_total_snapshot") is not None]
    if len(token_samples) < 2:
        return 200, {"ok": True, "available": False}

    def _avg_tps(from_ts: int) -> float:
        window = [s for s in token_samples if s["ts"] >= from_ts]
        if len(window) < 2:
            return 0.0
        total = 0
        for i in range(1, len(window)):
            d = window[i]["tokens_total_snapshot"] - window[i - 1]["tokens_total_snapshot"]
            if d > 0:
                total += d
        span = max(1, window[-1]["ts"] - window[0]["ts"])
        return total / span

    avg_tps_1m  = _avg_tps(now - 60)
    avg_tps_5m  = _avg_tps(now - 300)
    avg_tps_1h  = _avg_tps(now - 3600)
    avg_tps_24h = _avg_tps(now - 86400)

    # 60-bucket sparkline series : 1 min buckets over the last hour
    series = []
    peak_tps = 0.0
    peak_ts = 0
    for bucket_idx in range(60):
        bucket_start = now - (60 - bucket_idx) * 60
        bucket_end = bucket_start + 60
        in_bucket = [s for s in token_samples if bucket_start <= s["ts"] < bucket_end]
        if len(in_bucket) >= 2:
            d = in_bucket[-1]["tokens_total_snapshot"] - in_bucket[0]["tokens_total_snapshot"]
            span = max(1, in_bucket[-1]["ts"] - in_bucket[0]["ts"])
            tps = max(0.0, d / span)
            series.append(round(tps, 2))
            if tps > peak_tps:
                peak_tps = tps
                peak_ts = bucket_end
        else:
            series.append(0.0)

    return 200, {
        "ok": True,
        "available": True,
        "now": now,
        "avg_tps_1m":  round(avg_tps_1m, 2),
        "avg_tps_5m":  round(avg_tps_5m, 2),
        "avg_tps_1h":  round(avg_tps_1h, 2),
        "avg_tps_24h": round(avg_tps_24h, 2),
        "peak_tps":    round(peak_tps, 2),
        "peak_ts":     peak_ts,
        "series_1h":   series,
    }


# ────────────────────────── GET /api/llm/stats ────────────────────────────


def _parse_llamacpp_metrics(text: str) -> dict:
    """Parse a Prometheus-format text dump from llama-server's /metrics.

    Returns a dict of {metric_name_without_namespace: value} for the
    counters and gauges we care about. Ignores HELP/TYPE/comment lines.
    """
    result = {}
    for line in (text or "").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        # Lines look like : "llamacpp:tokens_predicted_total 67890"
        # Or with labels  : 'foo{bar="baz"} 1.0'
        parts = line.split()
        if len(parts) < 2:
            continue
        name = parts[0]
        try:
            val = float(parts[-1])
        except (ValueError, TypeError):
            continue
        # Strip namespace prefix (llamacpp:)
        if ":" in name:
            name = name.split(":", 1)[1]
        # Strip label suffix {foo="bar"}
        if "{" in name:
            name = name.split("{", 1)[0]
        # Integer if it looks like one
        result[name] = int(val) if val == int(val) else val
    return result


def _tokens_per_watt(tokens: float, avg_watts: float):
    """Compute tokens/W. Returns None if avg_watts == 0."""
    if avg_watts <= 0:
        return None
    return tokens / avg_watts


def handle_llm_stats(ctx: dict) -> Response:
    """Fetch llama-server /metrics if LLM_SERVER_URL is configured.

    Returns: {available, model, tokens_generated_total, prompt_tokens_total,
             tokens_per_watt_avg (if storage available)}
    """
    cfg = ctx.get("config")
    url = (cfg.get("LLM_SERVER_URL", "") if cfg else "").strip().rstrip("/")
    if not url:
        return 200, {"available": False, "reason": "LLM_SERVER_URL not configured"}

    import urllib.request
    import urllib.error
    try:
        with urllib.request.urlopen(f"{url}/metrics", timeout=2) as r:
            text = r.read().decode("utf-8", errors="replace")
    except Exception as e:
        return 200, {"available": False, "reason": f"unreachable: {e}"}

    parsed = _parse_llamacpp_metrics(text)
    if not parsed:
        return 200, {"available": False, "reason": "no recognized metrics"}

    tokens_gen = parsed.get("tokens_predicted_total", 0)
    tokens_prompt = parsed.get("prompt_tokens_total", 0)

    # tokens/W (efficiency) using last-hour avg power if storage is available
    tokens_per_watt = None
    storage = ctx.get("storage")
    if storage is not None and tokens_gen > 0:
        import time as _t
        now = int(_t.time())
        recent = storage.get_samples(from_ts=now - 3600, to_ts=now)
        powers = [s.get("power") for s in recent if s.get("power")]
        if powers:
            avg_w = sum(powers) / len(powers)
            tokens_per_watt = _tokens_per_watt(tokens_gen, avg_w)

    return 200, {
        "available": True,
        "tokens_generated_total": tokens_gen,
        "prompt_tokens_total": tokens_prompt,
        "tokens_per_watt": round(tokens_per_watt, 2) if tokens_per_watt else None,
        "raw_metrics_count": len(parsed),
    }


# ─── R&D #8.4 — llama-bench history + regression detector ────────────────────
def handle_llamabench_status(ctx: dict) -> Response:
    """Status of the llama-bench monitor : binary presence + recent history."""
    from ..modules import llama_bench as _lb
    bin_path = _lb.find_binary()
    runs: list = []
    # If user has a 'llama_bench_runs' table in storage, read it.
    # Optional schema — skip if not present.
    storage = ctx.get("storage")
    if storage:
        try:
            with storage._lock:
                rows = storage._conn.execute(
                    "SELECT ts, model, test, avg_ts, stddev_ts "
                    "FROM llama_bench_runs ORDER BY ts ASC LIMIT 200"
                ).fetchall()
                runs = [dict(r) for r in rows]
        except Exception:
            runs = []  # table doesn't exist yet, no harm
    regression = _lb.detect_regression(runs) if runs else None
    return 200, {
        "ok": True,
        "binary_available": bin_path is not None,
        "binary_path": bin_path,
        "runs_count": len(runs),
        "recent_runs": runs[-10:],
        "regression": regression,
    }


# ─── R&D #8.7 — Jupyter kernel monitor ───────────────────────────────────────
def handle_jupyter_kernels(ctx: dict) -> Response:
    """List Jupyter kernels with GPU attribution."""
    from ..modules import jupyter_monitor as _jm
    kernels = _jm.list_kernels()
    return 200, {
        "ok": True,
        "available": True,
        "count": len(kernels),
        "kernels": kernels,
    }


# ─── R&D #8.1 — History scrubber (snapshot at past timestamp) ────────────────
def handle_snapshot_at(ctx: dict, params: Optional[dict] = None) -> Response:
    """Return the GPU sample CLOSEST (within ±60s by default) to the
    requested timestamp.

    Query params :
      t           = unix epoch seconds (required)
      gpu_index   = GPU to query (default 0)
      tolerance   = max distance in seconds (default 60)

    Response :
      {ok, found, t_requested, t_actual, distance_s, sample: {...}}
      404 if no sample within tolerance.
    """
    storage = ctx.get("storage")
    if not storage:
        return 503, {"ok": False, "error": "storage unavailable"}
    params = params or {}
    try:
        t_req = int(float(params.get("t", "0")))
    except (ValueError, TypeError):
        return 400, {"ok": False, "error": "t must be a unix timestamp"}
    if t_req <= 0:
        return 400, {"ok": False, "error": "t required"}
    try:
        gpu_index = int(params.get("gpu_index", "0"))
    except (ValueError, TypeError):
        gpu_index = 0
    try:
        tolerance = int(params.get("tolerance", "60"))
        tolerance = max(1, min(3600, tolerance))
    except (ValueError, TypeError):
        tolerance = 60

    # Find sample within tolerance window, closest to t_req
    cols = ",".join(["ts", "temp", "fan_pct", "fan0_rpm", "fan1_rpm",
                     "clk_gpu", "clk_mem", "power", "power_limit",
                     "util_gpu", "mem_used_mib", "tokens_total_snapshot"])
    with storage._lock:
        row = storage._conn.execute(
            f"SELECT {cols} FROM samples "
            f"WHERE gpu_index = ? AND ts BETWEEN ? AND ? "
            f"ORDER BY ABS(ts - ?) ASC LIMIT 1",
            (gpu_index, t_req - tolerance, t_req + tolerance, t_req),
        ).fetchone()
    if row is None:
        return 404, {
            "ok": False, "found": False, "t_requested": t_req,
            "error": f"no sample within ±{tolerance}s",
        }
    sample = dict(row)
    distance = abs(sample["ts"] - t_req)
    return 200, {
        "ok": True, "found": True,
        "t_requested": t_req,
        "t_actual": sample["ts"],
        "distance_s": distance,
        "sample": sample,
    }
