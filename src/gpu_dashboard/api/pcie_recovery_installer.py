"""HTTP handler — F4.4 one-click install of the PCIe recovery wrapper."""
from __future__ import annotations

from typing import Any, Optional, Tuple

Response = Tuple[int, Any]


def handle_pcie_recovery_install_wrapper(
        ctx: dict,
        body: Optional[dict] = None) -> Response:
    from ..modules import pcie_recovery_installer
    body = body or {}
    password = body.get("password") or ""
    if not isinstance(password, str):
        return 400, {"ok": False,
                      "error": "bad_password_type",
                      "message": "password must be a string"}
    if not password:
        return 400, {"ok": False,
                      "error": "empty_password",
                      "message": "password is required"}
    result = pcie_recovery_installer.install_wrapper(password)
    return 200, result
