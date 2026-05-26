"""HTTP handlers — F4.2 Recovery Modal V1.

Three endpoints to support the modal UI:

  GET  /api/pcie-recovery/check-wrapper  → {available: bool}
  POST /api/pcie-recovery/run-step       → run a step, return result
  GET  /api/pcie-recovery/check-link     → re-check link state
"""
from __future__ import annotations

import json
from typing import Any, Optional, Tuple

Response = Tuple[int, Any]


def handle_pcie_recovery_check_wrapper(ctx: dict) -> Response:
    from ..modules import pcie_recovery_runner
    return 200, {"available": pcie_recovery_runner.is_wrapper_available(),
                  "wrapper_path": pcie_recovery_runner.WRAPPER_PATH}


def handle_pcie_recovery_run_step(ctx: dict,
                                     body: Optional[dict] = None) -> Response:
    from ..modules import pcie_recovery_runner
    body = body or {}
    step_id = (body.get("step_id") or "").strip()
    bdf = (body.get("bdf") or "").strip() or None
    if not step_id:
        return 400, {"ok": False,
                      "error": "missing_step_id",
                      "message": "POST body must include step_id"}
    result = pcie_recovery_runner.run_step(step_id, bdf=bdf)
    # 200 even on step failure — frontend uses result.ok to decide
    # what to display. 4xx/5xx is reserved for the API contract
    # being violated (missing param, server error).
    return 200, result


def handle_pcie_recovery_check_link(ctx: dict) -> Response:
    from ..modules import pcie_recovery_advisor
    return 200, pcie_recovery_advisor.status(ctx.get("config"))
