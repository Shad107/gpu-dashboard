"""HTTP handlers for /api/driver-vault (R&D #16.4)."""
from __future__ import annotations

import os
from typing import Optional, Tuple


Response = Tuple[int, dict]


def handle_driver_vault_status(ctx: dict) -> Response:
    from ..modules import driver_vault
    return 200, driver_vault.status()


def handle_driver_vault_stash(ctx: dict) -> Response:
    from ..modules import driver_vault
    return 200, driver_vault.stash_current_deb()


def handle_driver_vault_rollback_script(ctx: dict, params: Optional[dict] = None) -> Response:
    """Generate a bash rollback script targeting a vaulted .deb.

    Query params :
      name : basename of the .deb in the vault (path-traversal-safe)
    """
    from ..modules import driver_vault
    params = params or {}
    name = params.get("name", "")
    if not name or "/" in name or ".." in name:
        return 400, {"ok": False, "error": "valid 'name' (basename) required"}
    target = os.path.join(driver_vault.vault_dir(), name)
    if not os.path.isfile(target):
        return 404, {"ok": False, "error": "no such vaulted .deb"}
    cur = driver_vault.current_driver()
    if not cur:
        return 503, {"ok": False, "error": "current driver not detected"}
    script = driver_vault.build_rollback_script(target, cur["package"])
    return 200, {"ok": True, "script": script, "target": target,
                  "current_package": cur["package"]}
