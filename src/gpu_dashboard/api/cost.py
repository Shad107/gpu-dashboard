"""HTTP handlers for electricity cost + energy aggregation.

Extracted from the legacy monolith in cycle 6 of the api/ split.
Covers electricity pricing config, kWh/cost windows, year-to-date
energy stats, and the power-by-hour heatmap.
"""
from __future__ import annotations

import json
import os
import time
from typing import Optional, Tuple

from . import _core as _m


Response = Tuple[int, dict]


def _parse_gpu_index(params):
    return _m._parse_gpu_index(params)


# ────────────────────────── GET /api/power-stats ───────────────────────────


def handle_power_stats(ctx: dict, params: Optional[dict] = None) -> Response:
    """Power aggregates over 24h + 24-point downsampled series + cost today."""
    storage = ctx.get("storage")
    if storage is None:
        return 503, {"ok": False, "error": "storage not available"}
    cfg = ctx.get("config")

    import time as _time
    import datetime as _dt
    now = int(_time.time())
    gpu = _parse_gpu_index(params or {})

    today_start = int(_dt.datetime.fromtimestamp(now).replace(
        hour=0, minute=0, second=0, microsecond=0).timestamp())
    month_start = int(_dt.datetime.fromtimestamp(now).replace(
        day=1, hour=0, minute=0, second=0, microsecond=0).timestamp())
    year_start = int(_dt.datetime.fromtimestamp(now).replace(
        month=1, day=1, hour=0, minute=0, second=0, microsecond=0).timestamp())

    samples_24h = storage.get_samples(from_ts=now - 86400, to_ts=now, gpu_index=gpu)
    powers = [s["power"] for s in samples_24h if s.get("power") is not None]
    avg_w = sum(powers) / len(powers) if powers else 0
    peak_w = max(powers) if powers else 0
    peak_ts = 0
    for s in samples_24h:
        if s.get("power") == peak_w:
            peak_ts = s["ts"]
            break

    today_samples = [s for s in samples_24h if s["ts"] >= today_start and s.get("power") is not None]
    wh = 0.0
    for i in range(1, len(today_samples)):
        prev = today_samples[i - 1]
        cur = today_samples[i]
        dt = min(cur["ts"] - prev["ts"], 300)
        avg = (prev["power"] + cur["power"]) / 2
        wh += avg * dt / 3600
    kwh_today = wh / 1000

    price = 0.25
    currency = "EUR"
    if cfg is not None:
        try:
            price = float(cfg.get("ELECTRICITY_PRICE_EUR_PER_KWH", default="0.25"))
        except (ValueError, TypeError):
            price = 0.25
        currency = cfg.get("ELECTRICITY_CURRENCY", default="EUR") or "EUR"

    cost_today = round(kwh_today * price, 4)

    # Yearly kWh — integrate over all year-to-date samples (single query)
    year_samples = storage.get_samples(from_ts=year_start, to_ts=now, gpu_index=gpu)
    year_wh = 0.0
    for i in range(1, len(year_samples)):
        prev_s = year_samples[i - 1]
        cur = year_samples[i]
        if prev_s.get("power") is None or cur.get("power") is None:
            continue
        dt = min(cur["ts"] - prev_s["ts"], 300)
        avg = (prev_s["power"] + cur["power"]) / 2
        year_wh += avg * dt / 3600
    kwh_year = year_wh / 1000
    cost_year = round(kwh_year * price, 4)  # match kwh_today precision

    # Monthly kWh — integrate over month-to-date samples
    month_samples = storage.get_samples(from_ts=month_start, to_ts=now, gpu_index=gpu)
    month_wh = 0.0
    for i in range(1, len(month_samples)):
        prev_s = month_samples[i - 1]
        cur = month_samples[i]
        if prev_s.get("power") is None or cur.get("power") is None:
            continue
        dt = min(cur["ts"] - prev_s["ts"], 300)
        avg = (prev_s["power"] + cur["power"]) / 2
        month_wh += avg * dt / 3600
    kwh_month = month_wh / 1000
    cost_month = round(kwh_month * price, 4)

    # Budget tracker — forecast end-of-month from linear extrapolation
    budget_kwh = 0.0
    if cfg is not None:
        try:
            budget_kwh = float(cfg.get("ELECTRICITY_MONTHLY_BUDGET_KWH", default="0") or 0)
        except (ValueError, TypeError):
            budget_kwh = 0.0
    # Days in current month for end-of-month timestamp
    import calendar as _cal
    cur_dt = _dt.datetime.fromtimestamp(now)
    days_in_month = _cal.monthrange(cur_dt.year, cur_dt.month)[1]
    month_end_ts = int(_dt.datetime(
        cur_dt.year, cur_dt.month, days_in_month, 23, 59, 59
    ).timestamp())
    month_total_s = max(1, month_end_ts - month_start)
    month_elapsed_s = max(1, min(month_total_s, now - month_start))
    month_progress_pct = round(month_elapsed_s / month_total_s * 100, 1)
    forecast_kwh = round(kwh_month / (month_elapsed_s / month_total_s), 2) if kwh_month > 0 else 0.0
    over_budget = budget_kwh > 0 and forecast_kwh > budget_kwh

    series = []
    for h in range(24):
        bucket_start = now - (24 - h) * 3600
        bucket_end = bucket_start + 3600
        in_bucket = [s["power"] for s in samples_24h
                     if bucket_start <= s["ts"] < bucket_end and s.get("power") is not None]
        series.append(round(sum(in_bucket) / len(in_bucket), 1) if in_bucket else 0)

    return 200, {
        "ok": True,
        "avg_watts_24h": round(avg_w, 1),
        "peak_watts_24h": round(peak_w, 1),
        "peak_ts": peak_ts,
        "kwh_today": round(kwh_today, 4),
        "cost_today": cost_today,
        "kwh_year": round(kwh_year, 4),  # 4-decimal precision matches kwh_today
        "cost_year": cost_year,
        "year_start_ts": year_start,
        "kwh_month": round(kwh_month, 4),
        "cost_month": cost_month,
        "month_start_ts": month_start,
        "month_end_ts": month_end_ts,
        "month_progress_pct": month_progress_pct,
        "forecast_kwh": forecast_kwh,
        "budget_kwh": round(budget_kwh, 2),
        "over_budget": over_budget,
        "currency": currency,
        "price_per_kwh": price,
        "series_24h": series,
        "samples_count": len(samples_24h),
    }


# ────────────────────────── GET /api/power-heatmap ────────────────────────


def handle_power_heatmap(ctx: dict, params: dict) -> Response:
    """24-bucket heatmap of avg power + cost by hour-of-day over last N days.

    Useful for spotting patterns like 'training runs every 5am eat €0.50/day'
    or 'weekday afternoons are when inference is most active'.

    Query params : days (default 7)
    Returns :
      days: int
      currency: str
      price_per_kwh: float
      hours: [{hour, avg_watts, kwh_per_hour, cost_per_hour, sample_count}, ...]  (length 24)
    """
    storage = ctx.get("storage")
    if storage is None:
        return 503, {"ok": False, "error": "storage not available"}
    cfg = ctx.get("config")

    try:
        days = int(params.get("days", 7))
    except (ValueError, TypeError):
        return 400, {"ok": False, "error": "days must be integer"}
    if days < 1 or days > 365:
        return 400, {"ok": False, "error": "days out of range [1, 365]"}

    import time as _time
    now = int(_time.time())
    from_ts = now - days * 86400

    # Use storage to query all samples in window (no resampling)
    gpu = _parse_gpu_index(params)
    samples = storage.get_samples(from_ts=from_ts, to_ts=now, gpu_index=gpu)

    # Bucket by hour-of-day using local time
    import datetime as _dt
    buckets = [{"watts_sum": 0.0, "count": 0} for _ in range(24)]
    for s in samples:
        if s.get("power") is None:
            continue
        ts = s.get("ts", 0)
        h = _dt.datetime.fromtimestamp(ts).hour
        buckets[h]["watts_sum"] += s["power"]
        buckets[h]["count"] += 1

    # Rate + currency
    price = 0.25
    currency = "EUR"
    if cfg is not None:
        try:
            price = float(cfg.get("ELECTRICITY_PRICE_EUR_PER_KWH", default="0.25"))
        except (ValueError, TypeError):
            price = 0.25
        currency = cfg.get("ELECTRICITY_CURRENCY", default="EUR") or "EUR"

    hours_out = []
    for h in range(24):
        b = buckets[h]
        avg_w = (b["watts_sum"] / b["count"]) if b["count"] > 0 else 0.0
        kwh = avg_w / 1000.0  # kWh consumed during 1 hour at this avg power
        cost = kwh * price
        hours_out.append({
            "hour": h,
            "avg_watts": round(avg_w, 1),
            "kwh_per_hour": round(kwh, 4),
            "cost_per_hour": round(cost, 4),
            "sample_count": b["count"],
        })

    return 200, {
        "ok": True,
        "days": days,
        "currency": currency,
        "price_per_kwh": price,
        "hours": hours_out,
    }


# ────────────────────────── POST /api/electricity/config ──────────────────


def handle_electricity_config(ctx: dict, payload: dict) -> Response:
    """Update electricity price + currency at runtime.

    Persists to config.env so the change survives restart. Also updates the
    in-memory Config so /api/electricity uses the new rate immediately
    (no restart required for this specific setting).
    """
    cfg = ctx.get("config")
    if cfg is None:
        return 500, {"ok": False, "error": "no config loaded"}

    try:
        price = float(payload.get("price_per_kwh"))
    except (ValueError, TypeError):
        return 400, {"ok": False, "error": "price_per_kwh must be a number"}
    if not (0 < price < 5):  # sanity check (0 < x < 5 €/kWh)
        return 400, {"ok": False, "error": "price_per_kwh out of reasonable range (0, 5)"}

    currency = str(payload.get("currency", "EUR")).strip().upper() or "EUR"
    if len(currency) > 4:
        return 400, {"ok": False, "error": "currency code too long"}

    # Optional monthly budget (cycle 121) — 0 disables tracking
    budget_kwh = 0.0
    if "budget_kwh" in payload:
        try:
            budget_kwh = float(payload.get("budget_kwh") or 0)
        except (ValueError, TypeError):
            return 400, {"ok": False, "error": "budget_kwh must be a number"}
        if budget_kwh < 0 or budget_kwh > 10000:
            return 400, {"ok": False, "error": "budget_kwh out of range (0, 10000)"}

    # 1) Update in-memory Config (effective immediately)
    cfg.set("ELECTRICITY_PRICE_EUR_PER_KWH", price)
    cfg.set("ELECTRICITY_CURRENCY", currency)
    if "budget_kwh" in payload:
        cfg.set("ELECTRICITY_MONTHLY_BUDGET_KWH", budget_kwh)

    # 2) Persist to config.env so it survives restart
    config_path = ctx.get("config_path") or os.path.expanduser(
        "~/.config/gpu-dashboard/config.env"
    )
    try:
        os.makedirs(os.path.dirname(config_path), exist_ok=True)
        existing = {}
        if os.path.isfile(config_path):
            from ..config import parse_env_file
            existing = parse_env_file(config_path)
        existing["ELECTRICITY_PRICE_EUR_PER_KWH"] = str(price)
        existing["ELECTRICITY_CURRENCY"] = currency
        if "budget_kwh" in payload:
            existing["ELECTRICITY_MONTHLY_BUDGET_KWH"] = str(budget_kwh)
        from ..config import write_env_file
        write_env_file(config_path, existing,
                       header="# Auto-updated by gpu-dashboard /api/electricity/config")
    except OSError as e:
        return 500, {"ok": False, "error": f"could not write config.env: {e}"}

    return 200, {"ok": True, "price_per_kwh": price, "currency": currency,
                 "budget_kwh": budget_kwh,
                 "config_path": config_path}


# ────────────────────────── GET /api/electricity ──────────────────────────


def handle_electricity(ctx: dict, params: dict) -> Response:
    """Compute energy consumed + cost over a window from stored samples.

    Default window is the last 1 hour. Returns avg power, kWh consumed,
    cost in the configured currency, plus 24h + 30d extrapolations.
    """
    storage = ctx.get("storage")
    if storage is None:
        return 503, {"ok": False, "error": "storage not available"}
    cfg = ctx.get("config")
    try:
        window = int(params.get("since", 3600))
    except (ValueError, TypeError):
        return 400, {"ok": False, "error": "since must be integer (seconds)"}

    import time as _time
    now = int(_time.time())
    from_ts = now - window
    gpu = _parse_gpu_index(params)
    samples = storage.get_samples(from_ts=from_ts, to_ts=now, gpu_index=gpu)

    powers = [s.get("power") for s in samples if s.get("power") is not None]
    avg_w = (sum(powers) / len(powers)) if powers else 0.0
    # Energy = avg_W × duration_h
    kwh = (avg_w * window / 3600.0) / 1000.0  # kWh
    price = 0.25
    currency = "EUR"
    if cfg is not None:
        try:
            price = float(cfg.get("ELECTRICITY_PRICE_EUR_PER_KWH", default="0.25"))
        except (ValueError, TypeError):
            price = 0.25
        currency = cfg.get("ELECTRICITY_CURRENCY", default="EUR") or "EUR"
    cost = kwh * price

    # Extrapolations (assume avg power continues)
    daily_kwh = avg_w * 24 / 1000.0
    daily_cost = daily_kwh * price
    monthly_kwh = daily_kwh * 30
    monthly_cost = daily_cost * 30

    # Budget tracker (cycle 121) — month-to-date actual + forecast
    budget_kwh = 0.0
    if cfg is not None:
        try:
            budget_kwh = float(cfg.get("ELECTRICITY_MONTHLY_BUDGET_KWH", default="0") or 0)
        except (ValueError, TypeError):
            budget_kwh = 0.0

    import datetime as _dtm
    import calendar as _cal
    cur_dt = _dtm.datetime.fromtimestamp(now)
    month_start = int(cur_dt.replace(day=1, hour=0, minute=0, second=0, microsecond=0).timestamp())
    days_in_month = _cal.monthrange(cur_dt.year, cur_dt.month)[1]
    month_end_ts = int(_dtm.datetime(cur_dt.year, cur_dt.month, days_in_month, 23, 59, 59).timestamp())
    month_total_s = max(1, month_end_ts - month_start)
    month_elapsed_s = max(1, min(month_total_s, now - month_start))
    month_progress_pct = round(month_elapsed_s / month_total_s * 100, 1)
    # Integrate month-to-date samples for actual kWh
    month_samples = storage.get_samples(from_ts=month_start, to_ts=now, gpu_index=gpu)
    month_wh = 0.0
    for i in range(1, len(month_samples)):
        prev_s = month_samples[i - 1]
        cur_s = month_samples[i]
        if prev_s.get("power") is None or cur_s.get("power") is None:
            continue
        dt = min(cur_s["ts"] - prev_s["ts"], 300)
        avg = (prev_s["power"] + cur_s["power"]) / 2
        month_wh += avg * dt / 3600
    kwh_month = month_wh / 1000.0
    cost_month = kwh_month * price
    forecast_kwh = (kwh_month / (month_elapsed_s / month_total_s)) if kwh_month > 0 else 0.0
    over_budget = budget_kwh > 0 and forecast_kwh > budget_kwh

    return 200, {
        "ok": True,
        "window_seconds": window,
        "samples": len(samples),
        "avg_power_watts": round(avg_w, 2),
        "kwh": round(kwh, 4),
        "cost": round(cost, 4),
        "currency": currency,
        "price_per_kwh": price,
        "daily_kwh": round(daily_kwh, 3),
        "daily_cost": round(daily_cost, 3),
        "monthly_kwh": round(monthly_kwh, 2),
        "monthly_cost": round(monthly_cost, 2),
        "kwh_month": round(kwh_month, 3),
        "cost_month": round(cost_month, 3),
        "month_progress_pct": month_progress_pct,
        "forecast_kwh": round(forecast_kwh, 2),
        "budget_kwh": round(budget_kwh, 2),
        "over_budget": over_budget,
    }
