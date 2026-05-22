"""HTTP handlers for /api/dr-bundle (R&D #16.8)."""
from __future__ import annotations

import os
from typing import Optional, Tuple


Response = Tuple[int, dict]


def handle_dr_bundle_list(ctx: dict) -> Response:
    from ..modules import dr_bundle
    return 200, {"ok": True, "bundles": dr_bundle.list_bundles()}


def handle_dr_bundle_create(ctx: dict, payload: dict) -> Response:
    """Build a new DR bundle. Heavy operation — may take ~10-30s."""
    from ..modules import dr_bundle
    storage = ctx.get("storage")
    db_path = None
    if storage and hasattr(storage, "_path"):
        db_path = storage._path
    elif storage and hasattr(storage, "db_path"):
        db_path = storage.db_path
    try:
        from .. import __version__ as _ver
    except ImportError:
        _ver = "?"
    result = dr_bundle.build_bundle(history_db_path=db_path, version=_ver)
    if not result.get("ok"):
        return 502, result
    return 200, result


def handle_dr_bundle_delete(ctx: dict, name: str) -> Response:
    from ..modules import dr_bundle
    if dr_bundle.delete_bundle(name):
        return 200, {"ok": True, "deleted": name}
    return 404, {"ok": False, "error": "not found"}
