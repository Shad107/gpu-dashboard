"""HTTP handlers — F6 generalized installer endpoints."""
from __future__ import annotations

from typing import Any, Optional, Tuple

Response = Tuple[int, Any]


def handle_install_list(ctx: dict) -> Response:
    from ..modules import installer
    return 200, {"scripts": installer.list_available()}


def handle_install_check(ctx: dict,
                           params: Optional[dict] = None) -> Response:
    from ..modules import installer
    params = params or {}
    script_id = (params.get("script_id") or "").strip()
    if not script_id:
        return 400, {"ok": False,
                      "error": "missing_script_id",
                      "message": "query param script_id is required"}
    if script_id not in installer.SCRIPT_REGISTRY:
        return 400, {"ok": False,
                      "error": "not_in_registry",
                      "message": f"unknown script_id '{script_id}'"}
    return 200, {"ok": True,
                  "script_id": script_id,
                  "installed": installer.check_installed(script_id)}


def handle_install_run(ctx: dict,
                         body: Optional[dict] = None) -> Response:
    from ..modules import installer
    body = body or {}
    script_id = (body.get("script_id") or "").strip()
    password = body.get("password") or ""
    if not script_id:
        return 400, {"ok": False,
                      "error": "missing_script_id",
                      "message": "POST body must include script_id"}
    if not isinstance(password, str):
        return 400, {"ok": False,
                      "error": "bad_password_type",
                      "message": "password must be a string"}
    if not password:
        return 400, {"ok": False,
                      "error": "empty_password",
                      "message": "password is required"}
    if script_id not in installer.SCRIPT_REGISTRY:
        return 400, {"ok": False,
                      "error": "not_in_registry",
                      "message": f"unknown script_id '{script_id}'"}
    return 200, installer.install_script(script_id, password)
