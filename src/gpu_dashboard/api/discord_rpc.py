"""HTTP handler for /api/discord-rpc (R&D #15.7)."""
from __future__ import annotations

from typing import Optional, Tuple


Response = Tuple[int, dict]


def handle_discord_rpc_status(ctx: dict) -> Response:
    from ..modules import discord_rpc
    return 200, discord_rpc.status(ctx.get("config"))
