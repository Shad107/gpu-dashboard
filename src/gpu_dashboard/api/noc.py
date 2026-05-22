"""HTTP handler for the /noc NOC board (R&D #16.6).

A single self-contained HTML page designed for wall-mounted monitors :
oversized tiles per GPU, OK/WARN/CRIT color states, auto-refresh, no
chrome. Optimized to be readable from 3-5 m away.

Use cases :
  - Datacenter NOC big-screen status board
  - Homelab utility monitor (Pi + cheap screen → mounted on the wall)
  - Lab common-room display showing 'is the 3090 free ?'

Kiosk mode : Chromium --app=http://rig:9999/noc --kiosk
"""
from __future__ import annotations

import html
from typing import Optional, Tuple

from . import _core as _m


Response = Tuple[int, str]


def _gpus_available(*args, **kw):
    return _m._gpus_available(*args, **kw)


def _gpu_card_snapshot(*args, **kw):
    return _m._gpu_card_snapshot(*args, **kw)


def _verdict(temp: float, util: float) -> str:
    """Map (temp, util) to a tile state. Crit thresholds chosen to match
    R&D #3 alert defaults so /noc agrees with the rest of the dashboard."""
    if temp >= 85:
        return "crit"
    if temp >= 75 or util >= 95:
        return "warn"
    return "ok"


_COLORS = {
    "ok":   {"bg": "#0f3a17", "fg": "#a7f3a7", "border": "#34d34c"},
    "warn": {"bg": "#3d2c0a", "fg": "#fbd987", "border": "#fbbf24"},
    "crit": {"bg": "#3d0f12", "fg": "#fda4a4", "border": "#e05d44"},
    "off":  {"bg": "#1a1a1d", "fg": "#666",   "border": "#2a2a2d"},
}


def _render_tile(snap: dict, palette: dict) -> str:
    if not snap or not snap.get("alive"):
        col = palette["off"]
        return (
            f'<div class="noc-tile" style="background:{col["bg"]};border-color:{col["border"]};">'
            f'<div class="noc-name" style="color:{col["fg"]};">GPU offline</div>'
            f'<div class="noc-temp" style="color:{col["fg"]};">—</div>'
            "</div>"
        )
    name = (snap.get("name") or "GPU").replace("NVIDIA GeForce ", "").replace("NVIDIA ", "")
    short = name[:24]
    temp = snap.get("temp") or 0
    util = snap.get("util_gpu") or 0
    power = snap.get("power") or 0
    plim = snap.get("power_limit") or 0
    vram_used = (snap.get("mem_used_mib") or 0) / 1024
    vram_tot = (snap.get("mem_total_mib") or 0) / 1024
    v = _verdict(float(temp), float(util))
    col = palette[v]
    return (
        f'<div class="noc-tile" style="background:{col["bg"]};border-color:{col["border"]};">'
        f'<div class="noc-name" style="color:{col["fg"]};">{html.escape(short)}</div>'
        f'<div class="noc-temp" style="color:{col["fg"]};">{int(temp)}°C</div>'
        f'<div class="noc-row" style="color:{col["fg"]};">'
        f'  <span>{int(util)}% util</span>'
        f'  <span>{int(power)} / {int(plim)} W</span>'
        f'</div>'
        f'<div class="noc-sub" style="color:{col["fg"]};">'
        f'  VRAM {vram_used:.1f} / {vram_tot:.0f} GiB'
        f'</div>'
        "</div>"
    )


def handle_noc(ctx: dict, params: Optional[dict] = None) -> Response:
    """Render the NOC board HTML.

    Query params :
      refresh = seconds between auto-refresh (default 5, min 2, max 600)
      cols = forced column count (default auto based on GPU count)
    """
    params = params or {}
    try:
        refresh = max(2, min(600, int(params.get("refresh", "5"))))
    except (ValueError, TypeError):
        refresh = 5

    # Gather snapshots
    try:
        gpus = _gpus_available() or []
    except Exception:
        gpus = []
    snapshots: list = []
    for g in gpus:
        try:
            idx = int(g.get("index", g.get("idx", 0)))
        except (ValueError, TypeError):
            continue
        snapshots.append(_gpu_card_snapshot(gpu_index=idx))
    if not snapshots:
        snapshots = [{"alive": False}]

    # Choose layout : 1 col for 1 GPU, 2 cols for 2-4, 3 cols for 5-9, etc.
    # Parse the raw param first ; auto-detect when the user didn't supply one.
    try:
        cols_raw = int(params.get("cols", "0"))
    except (ValueError, TypeError):
        cols_raw = 0
    if cols_raw > 0:
        cols = max(1, min(6, cols_raw))
    else:
        n = len(snapshots)
        if n <= 1: cols = 1
        elif n <= 4: cols = 2
        elif n <= 9: cols = 3
        else: cols = 4

    tiles_html = "".join(_render_tile(s, _COLORS) for s in snapshots)

    # CSS — clamp() for big readable typography ; works from 3m away
    css = (
        "html,body{margin:0;background:#000;color:#fff;font-family:-apple-system,sans-serif;"
        "height:100%;overflow:hidden;}"
        ".noc-grid{display:grid;gap:2vw;padding:2vw;height:96vh;}"
        f".noc-grid{{grid-template-columns:repeat({cols}, 1fr);}}"
        ".noc-tile{border:0.4vw solid;border-radius:1vw;padding:2vw;"
        "display:flex;flex-direction:column;justify-content:center;align-items:center;}"
        ".noc-name{font-size:clamp(1.5rem,3vw,5rem);font-weight:600;margin-bottom:0.5vw;"
        "text-transform:uppercase;letter-spacing:0.05em;}"
        ".noc-temp{font-size:clamp(3rem,10vw,15rem);font-weight:800;"
        "font-variant-numeric:tabular-nums;line-height:1;margin:0.5vw 0;}"
        ".noc-row{display:flex;gap:3vw;font-size:clamp(1rem,2.5vw,3rem);"
        "font-variant-numeric:tabular-nums;margin-top:0.5vw;}"
        ".noc-sub{font-size:clamp(0.8rem,1.5vw,2rem);opacity:0.7;margin-top:0.3vw;}"
    )

    page = (
        '<!DOCTYPE html><html><head>'
        '<meta charset="utf-8">'
        f'<meta http-equiv="refresh" content="{refresh}">'
        '<meta name="viewport" content="width=device-width,initial-scale=1">'
        '<title>GreenWatts · NOC</title>'
        f'<style>{css}</style>'
        '</head><body>'
        f'<div class="noc-grid">{tiles_html}</div>'
        "</body></html>"
    )
    return 200, page
