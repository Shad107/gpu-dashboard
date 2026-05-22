"""HTTP handler for the read-only iframe-friendly /embed route (R&D #12.6).

A single-card minimalist HTML view designed to drop into Notion blocks,
status pages, blog posts, or anything that accepts an `<iframe>`.

Security :
  - Optional share-link gating via the R&D #9.3 signed-payload infra
    (?share=<token>). If config sets EMBED_REQUIRE_TOKEN=1 the route
    rejects unsigned requests with 401.
  - X-Frame-Options: SAMEORIGIN by default ; the server upgrades to
    ALLOWALL when a valid share-token is presented (so external
    embeds work).
  - No JS execution context : pure HTML refresh meta-tag (every 30s).

Variants :
  /embed/temp     — temperature gauge with color band
  /embed/power    — current power draw + power-limit context
  /embed/util     — utilization % bar
  /embed/llm      — LLM tokens/s + tok/Wh
  /embed/summary  — 4-tile mini dashboard (default)

Query params :
  share = signed share-link token (optional, see security above)
  refresh = seconds between auto-refresh (default 30, min 5, max 600)
  theme = light | dark (default dark)
"""
from __future__ import annotations

import html
from typing import Optional, Tuple

from . import _core as _m


Response = Tuple[int, str]


def _gpu_card_snapshot(*args, **kw):
    return _m._gpu_card_snapshot(*args, **kw)


_VALID_CARDS = ("summary", "temp", "power", "util", "llm")


def _temp_color(t: float) -> str:
    if t >= 80: return "#e05d44"   # red
    if t >= 70: return "#dfb317"   # yellow
    if t >= 50: return "#4c1"      # green
    return "#007ec6"               # cool blue


def _util_color(u: float) -> str:
    if u >= 90: return "#e05d44"
    if u >= 50: return "#dfb317"
    return "#4c1"


def _theme_palette(theme: str) -> dict:
    if theme == "light":
        return {
            "bg": "#fafafa", "fg": "#222", "muted": "#888",
            "card_bg": "#fff", "card_border": "#e0e0e0",
        }
    return {
        "bg": "#0e0e10", "fg": "#eee", "muted": "#888",
        "card_bg": "#1a1a1d", "card_border": "#2a2a2d",
    }


def _verify_share(token: str) -> Optional[dict]:
    """Validate a ?share=<token>. None if invalid or absent."""
    if not token:
        return None
    try:
        from ..modules.auth_tokens import verify_share_link
        return verify_share_link(token)
    except Exception:
        return None


def _render_card(title: str, value: str, sub: str, color: str, palette: dict) -> str:
    return (
        f'<div style="background:{palette["card_bg"]};'
        f'border:1px solid {palette["card_border"]};'
        f'border-radius:8px;padding:14px 18px;flex:1;min-width:120px;">'
        f'<div style="color:{palette["muted"]};font-size:0.78em;'
        f'text-transform:uppercase;letter-spacing:0.5px;">{html.escape(title)}</div>'
        f'<div style="color:{color};font-size:1.7em;font-weight:600;'
        f'font-variant-numeric:tabular-nums;margin-top:4px;">{html.escape(value)}</div>'
        f'<div style="color:{palette["muted"]};font-size:0.78em;margin-top:2px;">{html.escape(sub)}</div>'
        '</div>'
    )


def handle_embed(ctx: dict, card: str, params: Optional[dict] = None) -> Response:
    """Render the embed HTML. Returns (status_code, html_text)."""
    params = params or {}
    if card not in _VALID_CARDS:
        return 404, f"<html><body>unknown card: {html.escape(card)}</body></html>"

    cfg = ctx.get("config")
    require_token = False
    if cfg:
        require_token = (cfg.get("EMBED_REQUIRE_TOKEN", "0") in ("1", "true", "True"))

    share_payload = _verify_share(params.get("share", ""))
    if require_token and share_payload is None:
        return 401, "<html><body>token required (set ?share=...)</body></html>"

    try:
        refresh_s = max(5, min(600, int(params.get("refresh", "30"))))
    except (ValueError, TypeError):
        refresh_s = 30
    theme = params.get("theme", "dark")
    if theme not in ("dark", "light"):
        theme = "dark"
    palette = _theme_palette(theme)

    snap = _gpu_card_snapshot(gpu_index=0)
    if not snap or not snap.get("alive"):
        body = (
            f'<div style="color:{palette["muted"]};padding:18px;'
            f'font-family:sans-serif;">GPU offline</div>'
        )
    else:
        t = snap.get("temp", 0) or 0
        util = snap.get("util_gpu", 0) or 0
        power = snap.get("power", 0) or 0
        plim = snap.get("power_limit", 0) or 0
        vram_used = (snap.get("mem_used_mib", 0) or 0) / 1024
        vram_tot = (snap.get("mem_total_mib", 0) or 0) / 1024
        short_name = (snap.get("name", "GPU") or "GPU").replace(
            "NVIDIA GeForce ", "").replace("NVIDIA ", "")

        cards: list = []
        if card in ("summary", "temp"):
            cards.append(_render_card(
                "Temperature", f"{t}°C", short_name, _temp_color(t), palette,
            ))
        if card in ("summary", "power"):
            cards.append(_render_card(
                "Power", f"{power:.0f} W",
                f"/ {plim:.0f} W limit", "#a83f9f", palette,
            ))
        if card in ("summary", "util"):
            cards.append(_render_card(
                "Utilization", f"{util}%", "GPU compute", _util_color(util), palette,
            ))
        if card == "summary":
            cards.append(_render_card(
                "VRAM", f"{vram_used:.1f} GB",
                f"of {vram_tot:.0f} GB", "#4c8edf", palette,
            ))
        if card == "llm":
            llm_model = snap.get("llm_model") or "—"
            cards.append(_render_card(
                "LLM", str(llm_model)[:30], short_name, "#4c1", palette,
            ))

        body = (
            f'<div style="display:flex;gap:10px;padding:14px;flex-wrap:wrap;">'
            + "".join(cards)
            + "</div>"
        )

    sub_label = ""
    if share_payload:
        sub_label = (
            f'<div style="color:{palette["muted"]};font-size:0.7em;'
            f'text-align:right;padding:0 14px 8px;">'
            f'shared by {html.escape(str(share_payload.get("sub", "?"))[:20])}'
            "</div>"
        )

    page = (
        '<!DOCTYPE html><html><head>'
        '<meta charset="utf-8">'
        f'<meta http-equiv="refresh" content="{refresh_s}">'
        '<meta name="viewport" content="width=device-width,initial-scale=1">'
        f'<title>GreenWatts · {html.escape(card)}</title>'
        '<style>'
        f'html,body{{margin:0;background:{palette["bg"]};color:{palette["fg"]};'
        'font-family:-apple-system,sans-serif;}}'
        '</style>'
        '</head><body>'
        + body + sub_label +
        '</body></html>'
    )
    return 200, page
