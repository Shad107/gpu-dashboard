"""HTTP handlers for /api/boot-profile (R&D #15.8)."""
from __future__ import annotations

from typing import Optional, Tuple


Response = Tuple[int, dict]


def handle_boot_profile_status(ctx: dict) -> Response:
    from ..modules import boot_profile
    return 200, boot_profile.status()


def handle_boot_profile_save(ctx: dict, payload: dict) -> Response:
    """Save a profile to ~/.config/gpu-dashboard/boot_profile.json.

    Payload shape :
      {name, power_limit_w?, gpu_clock_offset_mhz?, mem_clock_offset_mhz?,
       persistence_mode?, fan_curve?}
    """
    from ..modules import boot_profile
    if not isinstance(payload, dict):
        return 400, {"ok": False, "error": "payload must be a dict"}
    # Mild validation
    if "name" not in payload or not str(payload["name"]).strip():
        return 400, {"ok": False, "error": "'name' is required"}
    if "power_limit_w" in payload:
        try:
            int(payload["power_limit_w"])
        except (ValueError, TypeError):
            return 400, {"ok": False, "error": "power_limit_w must be an integer"}
    boot_profile.save_profile(payload)
    return 200, {"ok": True, "saved_name": payload["name"]}


def handle_boot_profile_clear(ctx: dict) -> Response:
    from ..modules import boot_profile
    return 200, {"ok": True, "deleted": boot_profile.clear_profile()}


def handle_boot_profile_apply_now(ctx: dict) -> Response:
    """Trigger the apply pipeline immediately (no waiting for boot).
    Useful for testing the profile from the UI before scheduling it."""
    from ..modules import boot_profile
    prof = boot_profile.load_profile()
    if not prof:
        return 404, {"ok": False, "error": "no profile configured"}
    return 200, boot_profile.apply_profile(prof)
