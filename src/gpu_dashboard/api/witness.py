"""HTTP handlers — F2 State Witness endpoints.

Surfaces the state_witness module via:
  GET  /api/witness/list
  POST /api/witness/take      body: {reason?: str}
  GET  /api/witness/get?id=X
  GET  /api/witness/diff?before=A&after=B
"""
from __future__ import annotations

from typing import Any, Optional, Tuple

Response = Tuple[int, Any]


def handle_witness_list(ctx: dict) -> Response:
    from ..modules import state_witness
    return 200, {"ok": True, "snapshots": state_witness.list_snapshots()}


def handle_witness_take(ctx: dict,
                          body: Optional[dict] = None) -> Response:
    from ..modules import state_witness
    body = body or {}
    reason = (body.get("reason") or "manual").strip()[:40]
    if reason and not reason.replace("_", "").replace("-", "").isalnum():
        reason = "manual"
    snap = state_witness.take_snapshot(reason=reason or "manual")
    sid = state_witness.save_snapshot(snap)
    return 200, {
        "ok": True,
        "id": sid,
        "taken_at": snap.get("taken_at"),
        "reason": snap.get("reason"),
    }


def handle_witness_get(ctx: dict,
                         params: Optional[dict] = None) -> Response:
    from ..modules import state_witness
    params = params or {}
    sid = (params.get("id") or "").strip()
    if not sid:
        return 400, {"ok": False, "error": "missing_id",
                      "message": "query param 'id' is required"}
    snap = state_witness.load_snapshot(sid)
    if snap is None:
        return 404, {"ok": False, "error": "not_found",
                      "message": f"snapshot {sid!r} not found"}
    return 200, {"ok": True, "snapshot": snap}


def handle_witness_diff(ctx: dict,
                          params: Optional[dict] = None) -> Response:
    from ..modules import state_witness
    params = params or {}
    before = (params.get("before") or "").strip()
    after = (params.get("after") or "").strip()
    if not before or not after:
        return 400, {"ok": False, "error": "missing_ids",
                      "message": "params 'before' and 'after' are required"}
    return 200, state_witness.diff_snapshots(before, after)
