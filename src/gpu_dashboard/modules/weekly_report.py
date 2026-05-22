"""Module weekly_report — self-contained HTML + plain-text summary report (R&D #11.5).

Generates a printable digest of the last 7 days : uptime, kWh, cost,
peak temp, top models, alerts. Bundles inline SVG sparklines (no
external images).

Deliverable formats :
  - text/html (full markup, embeddable in email body)
  - text/plain (degraded terminal-readable alt)

stdlib only (html.escape, json, time, datetime).
"""
from __future__ import annotations

import datetime
import html
import time
from typing import Optional


NAME = "weekly_report"


def _spark_svg(values: list, width: int = 120, height: int = 24,
               color: str = "#4ade80") -> str:
    """Inline SVG sparkline. Values clipped to their own min..max."""
    if not values:
        return f'<svg width="{width}" height="{height}"></svg>'
    vmin = min(values)
    vmax = max(values)
    if vmax == vmin:
        vmax = vmin + 1
    n = len(values)
    points: list = []
    for i, v in enumerate(values):
        x = int(i / max(1, n - 1) * width) if n > 1 else width // 2
        y = int(height - ((v - vmin) / (vmax - vmin)) * height)
        points.append(f"{x},{y}")
    pts = " ".join(points)
    return (
        f'<svg width="{width}" height="{height}" xmlns="http://www.w3.org/2000/svg">'
        f'<polyline fill="none" stroke="{color}" stroke-width="1.5" points="{pts}"/>'
        f'</svg>'
    )


def _fmt_ago(secs: int) -> str:
    if secs < 60:
        return f"{secs}s"
    if secs < 3600:
        return f"{secs // 60}m"
    if secs < 86400:
        return f"{secs // 3600}h"
    return f"{secs // 86400}d"


def compute_stats(storage, days: int = 7) -> dict:
    """Aggregate the last `days` of samples + events into report stats."""
    now = int(time.time())
    since = now - days * 86400
    with storage._lock:
        # Aggregate samples
        rows = storage._conn.execute(
            "SELECT ts, temp, util_gpu, power, mem_used_mib "
            "FROM samples WHERE ts >= ? ORDER BY ts ASC",
            (since,),
        ).fetchall()
    samples = [dict(r) for r in rows]
    n = len(samples)
    if n == 0:
        return {"period_days": days, "sample_count": 0}

    temps = [s["temp"] for s in samples if s.get("temp") is not None]
    powers = [s["power"] for s in samples if s.get("power") is not None]
    utils = [s["util_gpu"] for s in samples if s.get("util_gpu") is not None]

    # Time interval between samples (assume sampler default = 5s)
    delta_s = 5
    energy_wh = sum(powers) * delta_s / 3600 if powers else 0.0

    # Recent alerts (events table)
    with storage._lock:
        alerts = storage._conn.execute(
            "SELECT ts, kind, payload FROM events WHERE ts >= ? "
            "AND kind LIKE 'alert.%' ORDER BY ts DESC LIMIT 20",
            (since,),
        ).fetchall()

    return {
        "period_days": days,
        "from_ts": since,
        "to_ts": now,
        "sample_count": n,
        "temp_max": max(temps) if temps else None,
        "temp_avg": round(sum(temps) / len(temps), 1) if temps else None,
        "power_max": max(powers) if powers else None,
        "power_avg": round(sum(powers) / len(powers), 1) if powers else None,
        "util_avg": round(sum(utils) / len(utils), 1) if utils else None,
        "energy_wh": round(energy_wh, 1),
        "alerts_count": len(alerts),
        "alerts": [dict(a) for a in alerts],
        "temp_series": temps[-100:],   # last 100 points for sparkline
        "power_series": powers[-100:],
    }


def render_html(stats: dict, cfg=None) -> str:
    """Self-contained HTML report. Embeddable in email body."""
    period = stats.get("period_days", 7)
    n = stats.get("sample_count", 0)
    if n == 0:
        return (
            f'<html><body><h1>GreenWatts — weekly report</h1>'
            f'<p>No samples in the last {period} days.</p></body></html>'
        )
    # Currency from config
    currency = "€"
    price = 0.25
    if cfg:
        try:
            currency = {"EUR": "€", "USD": "$", "GBP": "£"}.get(
                cfg.get("ELECTRICITY_CURRENCY", "EUR"), "€"
            )
            price = float(cfg.get("ELECTRICITY_PRICE_EUR_PER_KWH", "0.25") or "0.25")
        except (ValueError, TypeError):
            pass
    energy_kwh = stats["energy_wh"] / 1000
    cost = energy_kwh * price
    temp_spark = _spark_svg(stats.get("temp_series", []), color="#f87171")
    power_spark = _spark_svg(stats.get("power_series", []), color="#60a5fa")

    alerts_html = ""
    if stats.get("alerts"):
        rows = "".join(
            f'<tr><td>{html.escape(datetime.datetime.fromtimestamp(a["ts"]).isoformat(timespec="seconds"))}</td>'
            f'<td>{html.escape(a.get("kind", ""))}</td></tr>'
            for a in stats["alerts"][:10]
        )
        alerts_html = (
            f'<h2>Alerts ({stats["alerts_count"]})</h2>'
            f'<table border="0" cellpadding="4" style="font-family:sans-serif;font-size:13px;">'
            f'<thead><tr><th align="left">when</th><th align="left">kind</th></tr></thead>'
            f'<tbody>{rows}</tbody></table>'
        )
    return (
        '<html><body style="font-family:sans-serif;color:#222;">'
        '<h1 style="margin:0 0 6px 0">GreenWatts — weekly report</h1>'
        f'<p style="color:#888;margin:0 0 18px 0">Last {period} days · {n:,} samples</p>'
        '<table border="0" cellpadding="6" style="font-family:sans-serif;font-size:14px;border-collapse:collapse;">'
        f'<tr><td>⚡ Energy</td><td><b>{energy_kwh:.2f} kWh</b></td>'
        f'<td>Cost ({currency}/kWh × {price:.3f})</td><td><b>{cost:.2f} {currency}</b></td></tr>'
        f'<tr><td>🌡️ Temp avg / max</td><td><b>{stats.get("temp_avg", "n/a")}°C</b> / {stats.get("temp_max", "n/a")}°C</td>'
        f'<td>Sparkline</td><td>{temp_spark}</td></tr>'
        f'<tr><td>⚙️ Power avg / max</td><td><b>{stats.get("power_avg", "n/a")} W</b> / {stats.get("power_max", "n/a")} W</td>'
        f'<td>Sparkline</td><td>{power_spark}</td></tr>'
        f'<tr><td>📊 Util avg</td><td><b>{stats.get("util_avg", "n/a")}%</b></td><td></td><td></td></tr>'
        '</table>'
        f'{alerts_html}'
        '<p style="color:#aaa;font-size:11px;margin-top:24px">'
        'Generated by gpu-dashboard. Configure schedule in Settings → Reports.'
        '</p>'
        '</body></html>'
    )


def render_text(stats: dict, cfg=None) -> str:
    """Plain-text alt body for terminal mail clients."""
    period = stats.get("period_days", 7)
    n = stats.get("sample_count", 0)
    if n == 0:
        return f"GreenWatts weekly report\n\nNo samples in the last {period} days.\n"
    currency = "€"
    price = 0.25
    if cfg:
        try:
            currency = {"EUR": "€", "USD": "$", "GBP": "£"}.get(
                cfg.get("ELECTRICITY_CURRENCY", "EUR"), "€"
            )
            price = float(cfg.get("ELECTRICITY_PRICE_EUR_PER_KWH", "0.25") or "0.25")
        except (ValueError, TypeError):
            pass
    energy_kwh = stats["energy_wh"] / 1000
    cost = energy_kwh * price
    lines = [
        f"GreenWatts — weekly report (last {period} days, {n:,} samples)",
        "=" * 60,
        f"Energy      : {energy_kwh:.2f} kWh",
        f"Cost        : {cost:.2f} {currency}  (at {price:.3f} {currency}/kWh)",
        f"Temp        : avg {stats.get('temp_avg', 'n/a')}°C · max {stats.get('temp_max', 'n/a')}°C",
        f"Power       : avg {stats.get('power_avg', 'n/a')} W · max {stats.get('power_max', 'n/a')} W",
        f"Util        : avg {stats.get('util_avg', 'n/a')}%",
        f"Alerts      : {stats.get('alerts_count', 0)}",
    ]
    for a in (stats.get("alerts") or [])[:5]:
        try:
            ts = datetime.datetime.fromtimestamp(a["ts"]).isoformat(timespec="seconds")
        except (ValueError, KeyError):
            ts = "?"
        lines.append(f"  - {ts}  {a.get('kind', '')}")
    lines.append("")
    lines.append("Generated by gpu-dashboard.")
    return "\n".join(lines) + "\n"
