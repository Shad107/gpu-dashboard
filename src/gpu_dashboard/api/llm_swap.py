"""HTTP handlers for /api/llm-swap (R&D #17.5)."""
from __future__ import annotations

from typing import Optional, Tuple


Response = Tuple[int, dict]


def handle_llm_swap_status(ctx: dict) -> Response:
    from ..modules import llm_swap
    return 200, llm_swap.status(ctx.get("config"))


def handle_llm_swap_pin(ctx: dict, payload: dict) -> Response:
    from ..modules import llm_swap
    if not isinstance(payload, dict):
        return 400, {"ok": False, "error": "payload must be a dict"}
    name = str(payload.get("name", "")).strip()
    if not name:
        return 400, {"ok": False, "error": "'name' required"}
    action = str(payload.get("action", "pin"))
    if action == "pin":
        llm_swap.add_pin(name)
        return 200, {"ok": True, "pinned": name}
    if action == "unpin":
        removed = llm_swap.remove_pin(name)
        return 200, {"ok": True, "unpinned": name if removed else None}
    return 400, {"ok": False, "error": "action must be 'pin' or 'unpin'"}


def handle_llm_swap_suggest(ctx: dict, params: Optional[dict] = None) -> Response:
    from ..modules import llm_swap
    params = params or {}
    try:
        needed = int(params.get("needed_bytes", "0"))
    except (ValueError, TypeError):
        needed = 0
    if needed <= 0:
        return 400, {"ok": False, "error": "needed_bytes (>0) required"}
    loaded = llm_swap.probe_all(ctx.get("config"))
    return 200, llm_swap.suggest_evictions(loaded, needed)
